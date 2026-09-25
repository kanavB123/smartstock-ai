"""FastAPI server for SmartStock AI."""
import csv
import hashlib
import hmac
import io
import json
import logging
import math
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.demo import DEMO_DOCUMENTS, demo_sales_rows
from app.services import analysis_for_sales, anomalies, chunk_text, fallback_answer, normalise_sales_rows, rank_chunks
from app.advanced import executive_report_pdf, forecast_accuracy, inventory_recommendations, operational_alerts

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATABASE = DATA_DIR / "smartstock.db"
STATIC_DIR = ROOT / "static"
VALID_ORG_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
TOKEN_SECRET = os.getenv("SMARTSTOCK_TOKEN_SECRET", "change-this-in-production")
TOKEN_EXPIRY_SECONDS = int(os.getenv("SMARTSTOCK_TOKEN_EXPIRY", "86400"))  # 24 hours
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Schema version — bump when adding migration steps.
SCHEMA_VERSION = 3

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smartstock")

if ENVIRONMENT != "development" and TOKEN_SECRET == "change-this-in-production":
    logger.warning("SMARTSTOCK_TOKEN_SECRET is still set to the default — change it for production.")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def safe_org(value):
    value = (value or "demo").strip()[:50]
    if not value or any(char not in VALID_ORG_CHARS for char in value):
        raise HTTPException(400, "Organization ID may use letters, numbers, hyphens, and underscores only.")
    return value


@contextmanager
def db_connection():
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _run_migrations(db):
    """Apply incremental schema migrations based on a stored version number."""
    db.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    row = db.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
    current = int(row["value"]) if row else 0

    if current < 1:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY, organization_id TEXT NOT NULL, date TEXT NOT NULL,
            product TEXT NOT NULL, quantity REAL NOT NULL, inventory REAL
        );
        CREATE INDEX IF NOT EXISTS sales_org_idx ON sales(organization_id);
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY, organization_id TEXT NOT NULL, name TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, organization_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL, content TEXT NOT NULL, embedding_json TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );
        CREATE INDEX IF NOT EXISTS chunks_org_idx ON chunks(organization_id);
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, organization_id TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS answer_feedback (
            id INTEGER PRIMARY KEY, organization_id TEXT NOT NULL, question TEXT NOT NULL,
            rating INTEGER NOT NULL, comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Ensure embedding_json column exists (legacy upgrade path).
        fields = {row["name"] for row in db.execute("PRAGMA table_info(chunks)")}
        if "embedding_json" not in fields:
            db.execute("ALTER TABLE chunks ADD COLUMN embedding_json TEXT")
        current = 1

    if current < 2:
        db.execute("""
        CREATE TABLE IF NOT EXISTS inventory_config (
            id INTEGER PRIMARY KEY,
            organization_id TEXT NOT NULL,
            product TEXT NOT NULL,
            lead_time_days INTEGER NOT NULL DEFAULT 14,
            safety_days INTEGER NOT NULL DEFAULT 7,
            UNIQUE(organization_id, product)
        )
        """)
        current = 2

    if current < 3:
        db.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY,
            organization_id TEXT NOT NULL,
            product TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            lead_time_days INTEGER NOT NULL DEFAULT 14,
            lead_time_variability_days INTEGER NOT NULL DEFAULT 3,
            moq INTEGER NOT NULL DEFAULT 1,
            is_primary INTEGER NOT NULL DEFAULT 0
        )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS suppliers_org_idx ON suppliers(organization_id)")
        current = 3

    db.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)", (str(current),))
    db.connection.commit() if hasattr(db, "connection") else None


def initialise_database():
    with db_connection() as db:
        _run_migrations(db)
    logger.info("Database ready (schema v%d) at %s", SCHEMA_VERSION, DATABASE)


def _seed_demo_if_empty():
    """Pre-populate the demo workspace so first-time visitors see a full dashboard."""
    org_id = "demo"
    with db_connection() as db:
        count = db.execute("SELECT COUNT(*) AS count FROM sales WHERE organization_id = ?", (org_id,)).fetchone()["count"]
    if count > 0:
        return
    rows = demo_sales_rows()
    with db_connection() as db:
        db.executemany(
            "INSERT INTO sales (organization_id, date, product, quantity, inventory) VALUES (?, ?, ?, ?, ?)",
            [(org_id, row["date"], row["product"], row["quantity"], row["inventory"]) for row in rows],
        )
    for name, content in DEMO_DOCUMENTS:
        save_document(org_id, name, content)
    logger.info("Auto-seeded demo workspace (%d sales rows, %d documents)", len(rows), len(DEMO_DOCUMENTS))


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def sales_for(org_id):
    with db_connection() as db:
        return [dict(row) for row in db.execute("SELECT date, product, quantity, inventory FROM sales WHERE organization_id = ?", (org_id,))]


def chunks_for(org_id):
    with db_connection() as db:
        return [dict(row) for row in db.execute("""
            SELECT chunks.id, chunks.content, chunks.ordinal, chunks.embedding_json, documents.name
            FROM chunks JOIN documents ON documents.id = chunks.document_id
            WHERE chunks.organization_id = ?
        """, (org_id,))]


def inventory_config_for(org_id):
    """Return per-product inventory config as {product: {lead_time_days, safety_days}}."""
    with db_connection() as db:
        rows = db.execute("SELECT product, lead_time_days, safety_days FROM inventory_config WHERE organization_id = ?", (org_id,)).fetchall()
    return {row["product"]: {"lead_time_days": row["lead_time_days"], "safety_days": row["safety_days"]} for row in rows}


def suppliers_for(org_id):
    """Return all supplier rows for the org, grouped by product."""
    with db_connection() as db:
        rows = db.execute(
            "SELECT id, product, supplier_name, lead_time_days, lead_time_variability_days, moq, is_primary "
            "FROM suppliers WHERE organization_id = ? ORDER BY product, is_primary DESC, supplier_name",
            (org_id,),
        ).fetchall()
    result = defaultdict(list)
    for row in rows:
        result[row["product"]].append(dict(row))
    return dict(result)

# ---------------------------------------------------------------------------
# Embedding & retrieval helpers
# ---------------------------------------------------------------------------

def vector_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return 0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0


def optional_embeddings(inputs):
    """Use hosted semantic embeddings only when an application key is configured."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not inputs:
        return []
    try:
        from openai import OpenAI
        response = OpenAI(api_key=api_key).embeddings.create(
            model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"), input=inputs,
        )
        return [item.embedding for item in response.data]
    except Exception:
        logger.exception("Embedding request failed — falling back to lexical retrieval.")
        return []


def semantic_rank(question, chunks, limit=4):
    """Prefer cosine-ranked stored embeddings; retain lexical retrieval as a safe fallback."""
    query_vector = optional_embeddings([question])
    embedded = []
    if query_vector:
        for chunk in chunks:
            try:
                vector = json.loads(chunk.get("embedding_json") or "null")
            except (TypeError, json.JSONDecodeError):
                vector = None
            similarity = vector_similarity(query_vector[0], vector)
            if similarity > 0:
                embedded.append({**chunk, "score": round(similarity, 3)})
    if embedded:
        return sorted(embedded, key=lambda item: item["score"], reverse=True)[:limit]
    return rank_chunks(question, chunks, limit)


def live_analysis_chunks(org_id):
    """Expose computed inventory facts to RAG as cited, tenant-scoped context."""
    rows = sales_for(org_id)
    if not rows:
        return []
    chunks = []
    products = analysis_for_sales(rows)
    config = inventory_config_for(org_id)
    
    # 1. Product basics
    for product in products:
        inventory = "not provided" if product["latest_inventory"] is None else "{} units".format(product["latest_inventory"])
        cover = "not available" if product["days_cover"] is None else "{} days".format(product["days_cover"])
        status = "needs a reorder review" if product["reorder"] else "is currently on track"
        chunks.append({
            "id": "insight-{}".format(product["product"]),
            "name": "Live sales analysis — {}".format(product["product"]),
            "ordinal": 1,
            "content": (
                "Live sales analysis for {product}: total recorded sales {total} units; "
                "average daily demand {average} units; fourteen-day demand forecast {forecast} units; "
                "current inventory {inventory}; estimated stock cover {cover}; inventory status {status}."
            ).format(product=product["product"], total=product["total_sales"], average=product["daily_average"],
                     forecast=product["forecast_14d"], inventory=inventory, cover=cover, status=status),
        })
        
    # 2. Recommendations & Reorder queue
    anomaly_rows = anomalies(rows)
    recommendation_rows = []
    suppliers = suppliers_for(org_id)
    for product in products:
        pc = config.get(product["product"], {})
        recs = inventory_recommendations([product], lead_time_days=pc.get("lead_time_days", 14), safety_days=pc.get("safety_days", 7), anomalies_list=anomaly_rows, suppliers=suppliers)
        recommendation_rows.extend(recs)
        
    for rec in recommendation_rows:
        if rec["priority"] in ("Critical", "High"):
            reasons_str = "; ".join(rec.get("reasons", []))
            chunks.append({
                "id": "reorder-{}".format(rec["product"]),
                "name": "Live reorder queue — {}".format(rec["product"]),
                "ordinal": 1,
                "content": (
                    "Reorder Recommendation for {product}: Status is {priority}. "
                    "Current inventory: {inventory} units. Target stock: {target} units. "
                    "Recommended order quantity: {order} units. "
                    "Projected stockout in {stockout} days. Recommended action: {action}. "
                    "Reasons: {reasons}"
                ).format(product=rec["product"], priority=rec["priority"], inventory=rec["inventory"], 
                         target=rec["target_stock"], order=rec["recommended_order"], 
                         stockout=rec["estimated_stockout_days"], action=rec["action"], reasons=reasons_str),
            })

    # 3. Anomalies
    for index, anomaly in enumerate(anomaly_rows[:5]):
        chunks.append({
            "id": "anomaly-{}".format(index),
            "name": "Live anomaly detection — {}".format(anomaly["product"]),
            "ordinal": 1,
            "content": (
                "Unusual demand detected for {product}: {sales} units were sold on {date}. "
                "This is {z_score} standard deviations from the normal average."
            ).format(product=anomaly["product"], sales=anomaly["sales"], date=anomaly["date"], z_score=anomaly["z_score"]),
        })
        
    return chunks


def save_document(org_id, name, content):
    pieces = chunk_text(content)
    if not pieces:
        raise HTTPException(400, "The document did not contain readable text.")
    embeddings = optional_embeddings(pieces)
    with db_connection() as db:
        cursor = db.execute("INSERT INTO documents (organization_id, name, content) VALUES (?, ?, ?)", (org_id, name[:180], content))
        document_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO chunks (document_id, organization_id, ordinal, content, embedding_json) VALUES (?, ?, ?, ?, ?)",
            [(document_id, org_id, ordinal, piece, json.dumps(embeddings[ordinal - 1]) if len(embeddings) == len(pieces) else None) for ordinal, piece in enumerate(pieces, start=1)],
        )
    logger.info("Indexed document '%s' for org '%s' (%d chunks)", name[:60], org_id, len(pieces))
    return len(pieces)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[ChatMessage] = []


class SupplierRequest(BaseModel):
    product: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(min_length=1, max_length=100)
    lead_time_days: int = Field(ge=1, le=365)
    lead_time_variability_days: int = Field(ge=0, le=100)
    moq: int = Field(ge=1, le=1000000)
    is_primary: bool = False



class AuthRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=8, max_length=128)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=500)


class InventoryConfigRequest(BaseModel):
    product: str = Field(min_length=1, max_length=120)
    lead_time_days: int = Field(ge=1, le=365, default=14)
    safety_days: int = Field(ge=0, le=180, default=7)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 310_000).hex()
    return "{}${}".format(salt, digest)


def verify_password(password, stored):
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(password_hash(password, salt), stored)


def issue_token(user):
    exp = int(time.time()) + TOKEN_EXPIRY_SECONDS
    payload = "{}:{}:{}:{}".format(user["id"], user["organization_id"], user["role"], exp)
    signature = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return "{}.{}".format(payload, signature)


def verify_token(token):
    """Verify HMAC signature and optional expiry claim.  Returns identity dict."""
    if not token or "." not in token:
        raise HTTPException(401, "A workspace token is required.")
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid workspace token.")
    parts = payload.split(":")
    if len(parts) < 3:
        raise HTTPException(401, "Invalid workspace token.")
    try:
        user_id = int(parts[0])
        organization_id = parts[1]
        role = parts[2]
        exp = int(parts[3]) if len(parts) > 3 else None
    except (ValueError, IndexError):
        raise HTTPException(401, "Invalid workspace token.")
    if exp is not None and time.time() > exp:
        raise HTTPException(401, "Token has expired. Please log in again.")
    return {"id": user_id, "organization_id": organization_id, "role": role}


def get_org_id(x_workspace_token: Optional[str] = Header(None)) -> str:
    """Derive the organization from the auth token.

    If no token is provided the caller operates in the shared *demo*
    workspace.  This keeps the one-click demo functional while protecting
    authenticated tenants.
    """
    if not x_workspace_token:
        return "demo"
    identity = verify_token(x_workspace_token)
    return safe_org(identity["organization_id"])


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="SmartStock AI", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup():
    initialise_database()
    _seed_demo_if_empty()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "SmartStock AI"}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
def register(request: AuthRequest):
    organization_id = "org_{}".format(secrets.token_hex(5))
    with db_connection() as db:
        if db.execute("SELECT 1 FROM users WHERE email = ?", (request.email.lower(),)).fetchone():
            raise HTTPException(409, "An account with this email already exists.")
        db.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (organization_id, request.organization_name.strip()))
        cursor = db.execute("INSERT INTO users (organization_id, email, name, password_hash, role) VALUES (?, ?, ?, ?, ?)", (organization_id, request.email.lower(), request.name.strip(), password_hash(request.password), "admin"))
        user = {"id": cursor.lastrowid, "organization_id": organization_id, "role": "admin", "name": request.name.strip()}
    logger.info("Registered org '%s' (%s)", request.organization_name.strip(), organization_id)
    return {"token": issue_token(user), "user": user}


@app.post("/api/auth/login")
def login(request: LoginRequest):
    with db_connection() as db:
        user = db.execute("SELECT id, organization_id, name, role, password_hash FROM users WHERE email = ?", (request.email.lower(),)).fetchone()
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")
    payload = {"id": user["id"], "organization_id": user["organization_id"], "name": user["name"], "role": user["role"]}
    return {"token": issue_token(payload), "user": payload}


@app.get("/api/auth/me")
def me(x_workspace_token: Optional[str] = Header(None)):
    identity = verify_token(x_workspace_token)
    with db_connection() as db:
        user = db.execute("SELECT name, email, role FROM users WHERE id = ? AND organization_id = ?", (identity["id"], identity["organization_id"])).fetchone()
    if not user:
        raise HTTPException(401, "Account no longer exists.")
    return {"organization_id": identity["organization_id"], "name": user["name"], "email": user["email"], "role": user["role"]}


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

@app.post("/api/demo/reset")
def reset_demo():
    org_id = "demo"
    rows = demo_sales_rows()
    with db_connection() as db:
        db.execute("DELETE FROM sales WHERE organization_id = ?", (org_id,))
        db.execute("DELETE FROM chunks WHERE organization_id = ?", (org_id,))
        db.execute("DELETE FROM documents WHERE organization_id = ?", (org_id,))
        db.executemany(
            "INSERT INTO sales (organization_id, date, product, quantity, inventory) VALUES (?, ?, ?, ?, ?)",
            [(org_id, row["date"], row["product"], row["quantity"], row["inventory"]) for row in rows],
        )
    for name, content in DEMO_DOCUMENTS:
        save_document(org_id, name, content)
    logger.info("Demo workspace reset (%d sales rows, %d documents)", len(rows), len(DEMO_DOCUMENTS))
    return {"message": "Demo workspace is ready", "sales_rows": len(rows), "documents": len(DEMO_DOCUMENTS)}


@app.post("/api/demo/clear")
def clear_demo():
    """Remove all demo data so the user can upload their own."""
    org_id = "demo"
    with db_connection() as db:
        db.execute("DELETE FROM sales WHERE organization_id = ?", (org_id,))
        db.execute("DELETE FROM chunks WHERE organization_id = ?", (org_id,))
        db.execute("DELETE FROM documents WHERE organization_id = ?", (org_id,))
        db.execute("DELETE FROM inventory_config WHERE organization_id = ?", (org_id,))
    logger.info("Demo workspace cleared")
    return {"message": "Demo data cleared. Upload your own data to get started."}


# ---------------------------------------------------------------------------
# Business endpoints (org derived from auth token; defaults to 'demo')
# ---------------------------------------------------------------------------

@app.post("/api/sales")
async def upload_sales(
    file: UploadFile = File(...),
    mode: str = Form("append"),
    org_id: str = Depends(get_org_id),
):
    if mode not in ("append", "replace"):
        raise HTTPException(400, "Mode must be 'append' or 'replace'.")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file. Required columns: Date, Product, Quantity/Sales.")
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "CSV exceeds the 5 MB demo limit.")
    try:
        text = raw.decode("utf-8-sig")
        rows = normalise_sales_rows(csv.DictReader(io.StringIO(text)))
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(400, str(error))
    with db_connection() as db:
        if mode == "replace":
            db.execute("DELETE FROM sales WHERE organization_id = ?", (org_id,))
        db.executemany(
            "INSERT INTO sales (organization_id, date, product, quantity, inventory) VALUES (?, ?, ?, ?, ?)",
            [(org_id, row["date"], row["product"], row["quantity"], row["inventory"]) for row in rows],
        )
    logger.info("Sales upload (%s mode) for org '%s': %d rows", mode, org_id, len(rows))
    return {"message": "Sales data processed", "rows": len(rows), "products": len(set(row["product"] for row in rows)), "mode": mode}


@app.post("/api/documents")
async def upload_document(
    file: UploadFile = File(...),
    org_id: str = Depends(get_org_id),
):
    name = file.filename or "Untitled document"
    if not name.lower().endswith((".txt", ".md", ".csv", ".pdf")):
        raise HTTPException(400, "Upload a .txt, .md, .csv, or text-based .pdf knowledge document.")
    raw = await file.read()
    if len(raw) > 8_000_000:
        raise HTTPException(413, "Document exceeds the 8 MB limit.")
    if name.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if len(reader.pages) > 80:
                raise HTTPException(400, "PDF exceeds the 80-page limit.")
            content = "\n".join((page.extract_text() or "") for page in reader.pages)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Could not read this PDF. Upload a text-based, unencrypted PDF.")
    else:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(400, "Document must be UTF-8 encoded.")
    return {"message": "Document indexed", "chunks": save_document(org_id, name, content)}


@app.get("/api/overview")
def overview(org_id: str = Depends(get_org_id)):
    rows = sales_for(org_id)
    is_demo = org_id == "demo"
    if not rows:
        return {"ready": False, "is_demo": is_demo, "message": "Upload sales data or load the demo workspace."}
    products = analysis_for_sales(rows)
    total_sales = round(sum(row["quantity"] for row in rows), 1)
    with db_connection() as db:
        document_count = db.execute("SELECT COUNT(*) AS count FROM documents WHERE organization_id = ?", (org_id,)).fetchone()["count"]
    return {
        "ready": True,
        "is_demo": is_demo,
        "summary": {
            "total_sales": total_sales,
            "products": len(products),
            "reorder_alerts": sum(1 for product in products if product["reorder"]),
            "documents": document_count,
        },
        "products": products,
        "anomalies": anomalies(rows),
    }


@app.get("/api/intelligence")
def intelligence(org_id: str = Depends(get_org_id)):
    rows = sales_for(org_id)
    if not rows:
        return {"ready": False, "message": "Upload sales data to generate intelligence."}
    products = analysis_for_sales(rows)
    config = inventory_config_for(org_id)
    suppliers = suppliers_for(org_id)
    recommendation_rows = []
    anomaly_rows = anomalies(rows)
    for product in products:
        pc = config.get(product["product"], {})
        lt = pc.get("lead_time_days", 14)
        sd = pc.get("safety_days", 7)
        recs = inventory_recommendations([product], lead_time_days=lt, safety_days=sd, anomalies_list=anomaly_rows, suppliers=suppliers)
        recommendation_rows.extend(recs)
    # Re-sort by priority.
    priority_order = {"Critical": 0, "High": 1, "On track": 2}
    recommendation_rows.sort(key=lambda r: (priority_order.get(r["priority"], 9), -r["recommended_order"]))
    return {
        "ready": True,
        "accuracy": forecast_accuracy(rows),
        "recommendations": recommendation_rows,
        "alerts": operational_alerts(products, recommendation_rows, anomaly_rows),
    }


@app.post("/api/feedback")
def feedback(request: FeedbackRequest, org_id: str = Depends(get_org_id)):
    with db_connection() as db:
        db.execute("INSERT INTO answer_feedback (organization_id, question, rating, comment) VALUES (?, ?, ?, ?)", (org_id, request.question, request.rating, request.comment.strip()))
    return {"message": "Feedback saved. Thank you for improving the assistant."}


@app.post("/api/evaluations/run")
def run_evaluations(org_id: str = Depends(get_org_id)):
    cases = [
        ("What is the lead time for Webcam Pro?", "Supplier lead times"),
        ("When should we raise a reorder recommendation?", "Replenishment policy"),
        ("Which product responds to weekend promotions?", "Product notes"),
    ]
    available = chunks_for(org_id) + live_analysis_chunks(org_id)
    results = []
    for question, expected_source in cases:
        sources = semantic_rank(question, available)
        source_names = [source["name"] for source in sources]
        results.append({"question": question, "expected_source": expected_source, "sources": source_names, "passed": expected_source in source_names})
    passed = sum(1 for result in results if result["passed"])
    return {"total": len(results), "passed": passed, "score": round(passed / len(results) * 100), "results": results}


@app.get("/api/reports/executive")
def executive_report(org_id: str = Depends(get_org_id)):
    rows = sales_for(org_id)
    if not rows:
        raise HTTPException(400, "Upload sales data before creating a report.")
    products = analysis_for_sales(rows)
    summary = {
        "total_sales": round(sum(row["quantity"] for row in rows), 1), "products": len(products),
        "reorder_alerts": sum(1 for product in products if product["reorder"]),
    }
    with db_connection() as db:
        summary["documents"] = db.execute("SELECT COUNT(*) AS count FROM documents WHERE organization_id = ?", (org_id,)).fetchone()["count"]
        
    config = inventory_config_for(org_id)
    anomaly_rows = anomalies(rows)
    suppliers = suppliers_for(org_id)
    recommendation_rows = []
    for product in products:
        pc = config.get(product["product"], {})
        recs = inventory_recommendations([product], lead_time_days=pc.get("lead_time_days", 14), safety_days=pc.get("safety_days", 7), anomalies_list=anomaly_rows, suppliers=suppliers)
        recommendation_rows.extend(recs)
        
    priority_order = {"Critical": 0, "High": 1, "On track": 2}
    recommendation_rows.sort(key=lambda r: (priority_order.get(r["priority"], 9), -r["recommended_order"]))

    report_bytes = executive_report_pdf(summary, products, recommendation_rows, operational_alerts(products, recommendation_rows, anomaly_rows))
    headers = {"Content-Disposition": "attachment; filename=smartstock-executive-briefing.pdf"}
    return StreamingResponse(io.BytesIO(report_bytes), media_type="application/pdf", headers=headers)


@app.post("/api/chat")
def chat(request: ChatRequest, org_id: str = Depends(get_org_id)):
    search_query = request.question
    if request.history:
        search_query = f"{request.history[-1].content} {request.question}"
        
    sources = semantic_rank(search_query, chunks_for(org_id) + live_analysis_chunks(org_id))
    answer = fallback_answer(request.question, sources)
    model_used = "source-grounded fallback"
    follow_ups = []
    
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key and sources:
        try:
            from openai import OpenAI
            context = "\n\n".join("[{}] {}".format(source["name"], source["content"]) for source in sources)
            
            messages = [
                {
                    "role": "system", 
                    "content": (
                        "You are SmartStock AI. Answer only from the supplied context. "
                        "Prioritize facts that directly answer the user's product and intent. "
                        "Be concise, use Markdown formatting (bolding, lists), state uncertainty when needed, and do not invent business data.\n\n"
                        f"Retrieved context:\n{context}"
                    )
                }
            ]
            for msg in request.history[-4:]:
                messages.append({"role": msg.role, "content": msg.content})
                
            messages.append({
                "role": "user", 
                "content": request.question + "\n\nProvide a JSON response with two keys: 'answer' (markdown string) and 'follow_ups' (list of exactly 2 short relevant follow-up question strings)."
            })
            
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                response_format={"type": "json_object"},
                messages=messages,
                temperature=0.3
            )
            result_data = json.loads(response.choices[0].message.content)
            answer = result_data.get("answer", fallback_answer(request.question, sources))
            follow_ups = result_data.get("follow_ups", [])
            model_used = "OpenAI GPT-4o"
        except Exception:
            logger.exception("OpenAI synthesis failed — using fallback answer.")
            
    return {
        "answer": answer,
        "follow_ups": follow_ups,
        "mode": model_used,
        "sources": [{"name": source["name"], "chunk": source["ordinal"], "excerpt": source["content"][:220]} for source in sources],
    }


# ---------------------------------------------------------------------------
# Inventory configuration
# ---------------------------------------------------------------------------

@app.get("/api/inventory-config")
def get_inventory_config(org_id: str = Depends(get_org_id)):
    config = inventory_config_for(org_id)
    return {"configs": [{"product": k, **v} for k, v in config.items()]}


@app.post("/api/inventory-config")
def set_inventory_config(request: InventoryConfigRequest, org_id: str = Depends(get_org_id)):
    with db_connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO inventory_config (organization_id, product, lead_time_days, safety_days) VALUES (?, ?, ?, ?)",
            (org_id, request.product.strip(), request.lead_time_days, request.safety_days),
        )
    logger.info("Inventory config updated for '%s' in org '%s'", request.product.strip(), org_id)
    return {"message": "Configuration saved", "product": request.product.strip(), "lead_time_days": request.lead_time_days, "safety_days": request.safety_days}


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

@app.get("/api/suppliers")
def get_suppliers(org_id: str = Depends(get_org_id)):
    return {"suppliers": suppliers_for(org_id)}

@app.post("/api/suppliers")
def add_supplier(request: SupplierRequest, org_id: str = Depends(get_org_id)):
    with db_connection() as db:
        if request.is_primary:
            db.execute("UPDATE suppliers SET is_primary = 0 WHERE organization_id = ? AND product = ?", (org_id, request.product.strip()))
        cursor = db.execute(
            "INSERT INTO suppliers (organization_id, product, supplier_name, lead_time_days, lead_time_variability_days, moq, is_primary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (org_id, request.product.strip(), request.supplier_name.strip(), request.lead_time_days, request.lead_time_variability_days, request.moq, 1 if request.is_primary else 0)
        )
        supplier_id = cursor.lastrowid
    return {"message": "Supplier added", "id": supplier_id}

@app.delete("/api/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int, org_id: str = Depends(get_org_id)):
    with db_connection() as db:
        db.execute("DELETE FROM suppliers WHERE id = ? AND organization_id = ?", (supplier_id, org_id))
    return {"message": "Supplier deleted"}

# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@app.get("/api/export/sales")
def export_sales(org_id: str = Depends(get_org_id)):
    rows = sales_for(org_id)
    if not rows:
        raise HTTPException(400, "No sales data to export.")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "product", "quantity", "inventory"])
    writer.writeheader()
    writer.writerows(rows)
    headers = {"Content-Disposition": "attachment; filename=smartstock-sales-export.csv"}
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8")), media_type="text/csv", headers=headers)


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    if isinstance(exc, HTTPException):
        raise exc
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    raise HTTPException(500, "An internal error occurred. Please try again.")
