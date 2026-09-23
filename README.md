# SmartStock AI

**Live Demo:** [https://smartstock-ai-360w.onrender.com](https://smartstock-ai-360w.onrender.com)

SmartStock AI is a deployable final-year B.Tech project that combines sales forecasting, anomaly detection, inventory recommendations, and a citation-based RAG assistant.

## What is implemented

- CSV ingestion with input validation (`Date`, `Product`, `Quantity`/`Sales`; optional `Inventory`)
- Per-product 14-day least-squares demand forecast
- Low-stock/reorder signals and simple anomalous-demand detection
- Document ingestion for `.txt`, `.md`, and `.csv` files
- Tenant-scoped, query-expanded lexical RAG retrieval with returned source citations
- Live product-analysis facts are injected as cited RAG context, so questions about demand forecasts, stock cover, and reorder status are answered from current uploaded data
- Optional OpenAI Responses API synthesis. If unset/unavailable, a deterministic source-grounded fallback keeps the demo functional.
- One-click demo workspace and responsive dashboard

## Run locally

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`, then select **Load demo data**.

## Optional AI synthesis

Copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and export it before running the server. The server uses the Responses API with `store=False` and sends only the selected retrieved chunks to the model. Do not commit `.env` or expose its key in the browser.

```bash
export OPENAI_API_KEY="your_key_here"
export OPENAI_MODEL="gpt-5"
```

The implementation follows the [official OpenAI Responses API quickstart](https://developers.openai.com/api/docs/quickstart?site_locale=en).

## Deploy on Render

1. Create a new Git repository and push this project. Never push `.env`.
2. In Render, create a **Web Service** connected to the repository.
3. Choose Python 3, build command `pip install -r requirements.txt`, and start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
4. Add `OPENAI_API_KEY` only if you want model-generated answers; the demo works without it.
5. For a production deployment, replace local SQLite with managed Postgres, add real authentication, private object storage, and a persistent vector database.

## Data-security notes

This MVP scopes every row and knowledge chunk by `organization_id`, validates file type/size, and does not log uploaded file content. It intentionally is not a production multi-tenant security boundary: before handling company data, add authenticated identities, server-enforced organization membership, private storage, rate limits, audit logs, encryption/key policies, and a security review.

## CSV example

```csv
Date,Product,Quantity,Inventory
2026-09-01,Wireless Headphones,28,68
2026-09-02,Wireless Headphones,31,
2026-09-01,USB-C Hub,15,24
```
