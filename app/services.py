"""Pure analysis and retrieval helpers for SmartStock AI."""
import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import math
import re


TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")
STOP_WORDS = {"a", "an", "and", "are", "for", "how", "i", "in", "is", "it", "of", "on", "the", "to", "what", "when", "which", "with", "would"}
RELATED_TERMS = {
    "forecast": ("demand", "projected", "sales", "trend"),
    "demand": ("forecast", "projected", "sales", "trend"),
    "inventory": ("stock", "hand", "cover", "reorder"),
    "stock": ("inventory", "hand", "cover", "reorder"),
    "reorder": ("inventory", "stock", "safety", "buffer", "purchase"),
    "lead": ("supplier", "delivery", "days"),
    "time": ("lead", "supplier", "delivery", "days"),
    "supplier": ("lead", "delivery", "order", "days"),
    "minimum": ("order", "quantity", "moq"),
    "moq": ("minimum", "order", "quantity"),
    "promotion": ("weekend", "discount", "campaign"),
    "weekend": ("promotion", "discount", "campaign"),
}


def tokens(value):
    return TOKEN_RE.findall((value or "").lower())


def query_terms(query):
    """Expand useful business terms without losing product-name tokens."""
    base = [token for token in tokens(query) if token not in STOP_WORDS]
    expanded = list(base)
    for token in base:
        expanded.extend(RELATED_TERMS.get(token, ()))
    return base, expanded


def parse_date(value):
    """Accept common spreadsheet date formats and numeric Excel serials."""
    if value is None:
        raise ValueError("Date missing from the CSV")
    value = str(value).strip()
    if not value:
        raise ValueError("Date missing from the CSV")

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    try:
        numeric = float(value)
        if numeric > 0:
            origin = datetime(1899, 12, 30)
            return (origin + timedelta(days=numeric)).date()
    except ValueError:
        pass

    raise ValueError("Date must be YYYY-MM-DD, YYYY/MM/DD, DD/MM/YYYY, MM/DD/YYYY, or a readable Excel date value.")


SALES_COLUMN_ALIASES = {
    "date": ("date", "order date", "sales date", "transaction date", "date of transaction", "date of sale", "sale date", "order_date", "sales_date"),
    "product": ("product", "product name", "product description", "item", "item name", "item description", "product title", "sku", "product_name"),
    "quantity": (
        "quantity", "sales", "sales quantity", "sales qty", "units sold", "units_sold",
        "qty", "sales_quantity", "sales_qty", "qty sold", "quantity sold", "units", "unit",
        "units purchased", "number of units", "total quantity", "purchase quantity", "items sold",
        "unit sales", "total units sold"
    ),
    "inventory": (
        "inventory", "stock", "stock on hand", "on hand", "stock_on_hand", "on_hand", "stock onhand",
        "closing stock", "closing inventory", "available stock", "quantity on hand", "qty on hand",
        "current stock", "ending inventory"
    ),
}


def _canonical_field_name(raw_name):
    value = (raw_name or "").strip().lower().replace("-", " ").replace("_", " ")
    value = " ".join(value.split())
    return value


def infer_sales_column_mapping(fieldnames):
    """Map CSV headers to canonical fields, accepting only clear near matches."""
    normalized = {_canonical_field_name(name): name for name in fieldnames if name}
    mapping = {}
    used_headers = set()
    for standard_name, aliases in SALES_COLUMN_ALIASES.items():
        exact_matches = [alias for alias in normalized if alias in aliases]
        if len(exact_matches) == 1:
            mapping[standard_name] = normalized[exact_matches[0]]
            used_headers.add(exact_matches[0])
        elif len(exact_matches) > 1:
            used_headers.update(exact_matches)

    for standard_name, aliases in SALES_COLUMN_ALIASES.items():
        if standard_name in mapping:
            continue
        candidates = []
        for normalized_header, original_header in normalized.items():
            if normalized_header in used_headers:
                continue
            score = max(SequenceMatcher(None, normalized_header, alias).ratio() for alias in aliases)
            candidates.append((score, normalized_header, original_header))
        candidates.sort(reverse=True)
        if not candidates or candidates[0][0] < 0.82:
            continue
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.05:
            continue
        mapping[standard_name] = candidates[0][2]
        used_headers.add(candidates[0][1])
    return mapping


def _detect_csv_dialect(sample_text):
    sample = sample_text[:4096]
    for delimiter in (",", ";", "\t"):
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))
        except csv.Error:
            continue
        if len(rows) < 2:
            continue
        header = [cell.strip() for cell in rows[0]]
        if any(cell for cell in header):
            if delimiter == "," and len(header) == 1:
                continue
            return delimiter
    return ","


def parse_sales_csv_text(text):
    """Parse arbitrary sales CSV text into normalized rows."""
    if not text or not text.strip():
        raise ValueError("The uploaded CSV is empty.")
    dialect = _detect_csv_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=csv.excel if dialect == "," else None)
    if dialect != ",":
        reader = csv.DictReader(io.StringIO(text), delimiter=dialect)
    rows = list(reader)
    if not rows:
        raise ValueError("The CSV did not contain any rows.")
    return normalise_sales_rows(rows)


def sales_csv_column_mapping(text):
    """Return the detected header-to-field mapping for upload feedback."""
    if not text or not text.strip():
        return {}
    delimiter = _detect_csv_dialect(text)
    headers = next(csv.reader(io.StringIO(text), delimiter=delimiter), [])
    return infer_sales_column_mapping(headers)


def _parse_sales_number(value):
    """Parse common spreadsheet numbers with currency symbols and thousands commas."""
    cleaned = re.sub(r"[,\s$₹€£¥]", "", str(value or "").strip())
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-{}".format(cleaned[1:-1])
    return float(cleaned)


def normalise_sales_rows(rows):
    """Return validated date/product/quantity records from a CSV DictReader."""
    output = []
    rows = list(rows)
    headers = list(dict.fromkeys(key for raw in rows for key in (raw or {}).keys() if key is not None))
    overall_mapping = infer_sales_column_mapping(headers)
    for row_number, raw in enumerate(rows, start=2):
        raw = raw or {}
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        mapping = infer_sales_column_mapping(raw.keys())
        mapping = {**overall_mapping, **mapping}
        missing_fields = [name for name in ("date", "product", "quantity") if name not in mapping]
        if missing_fields:
            labels = {"date": "Date", "product": "Product", "quantity": "Quantity/Sales"}
            missing = ", ".join(labels[name] for name in missing_fields)
            detected = ", ".join(str(header) for header in raw if header)
            raise ValueError("Could not confidently match {} column(s) on CSV row {}. Detected headers: {}. Rename the closest headers or use Date, Product, Quantity/Sales.".format(missing, row_number, detected or "none"))
        row = {_canonical_field_name(key): (value or "").strip() for key, value in raw.items() if key is not None}

        def field(name):
            header = mapping.get(name)
            return row.get(_canonical_field_name(header), "") if header else ""

        try:
            date = parse_date(field("date"))
        except ValueError as error:
            raise ValueError("Invalid date on CSV row {}: {}".format(row_number, error)) from error
        product = field("product")
        if not product:
            raise ValueError("Product is blank on CSV row {}. Fill in the product name or SKU.".format(row_number))
        try:
            quantity = _parse_sales_number(field("quantity"))
        except ValueError as error:
            raise ValueError("Quantity/Sales must be numeric on CSV row {} (received {!r}).".format(row_number, field("quantity"))) from error
        inventory_value = field("inventory")
        try:
            inventory = _parse_sales_number(inventory_value) if inventory_value else None
        except ValueError:
            if inventory_value.lower() in ("n/a", "na", "none", "null", "-"):
                inventory = None
            else:
                raise ValueError("Inventory must be numeric on CSV row {} (received {!r}).".format(row_number, inventory_value))
        if quantity < 0:
            raise ValueError("Sales quantity cannot be negative")
        output.append({"date": date.isoformat(), "product": product[:120], "quantity": quantity, "inventory": inventory})
    if not output:
        raise ValueError("The CSV did not contain any sales rows")
    return output


def holt_winters_forecast(points, horizon=14, season_length=7, alpha=0.3, beta=0.1, gamma=0.3):
    """Triple exponential smoothing (additive) for seasonal demand forecasting.
    
    Falls back to linear_forecast when the series is too short for seasonal
    decomposition (needs at least 2 full seasonal cycles).
    """
    n = len(points)
    if n < season_length * 2:
        return linear_forecast(points, horizon)
    # Initialize level and trend from first season
    first_season = points[:season_length]
    second_season = points[season_length:season_length * 2]
    level = sum(first_season) / season_length
    trend = (sum(second_season) - sum(first_season)) / (season_length * season_length)
    # Initialize seasonal indices from first season
    seasonals = [points[i] - level for i in range(season_length)]
    # Smooth over observed data
    for i in range(season_length, n):
        value = points[i]
        prev_level = level
        season_index = i % season_length
        level = alpha * (value - seasonals[season_index]) + (1 - alpha) * (prev_level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonals[season_index] = gamma * (value - level) + (1 - gamma) * seasonals[season_index]
    # Forecast future values
    forecasted = []
    for k in range(1, horizon + 1):
        season_index = (n + k - 1) % season_length
        predicted = level + k * trend + seasonals[season_index]
        forecasted.append(max(0, round(predicted, 1)))
    return forecasted


def linear_forecast(points, horizon=14):
    """Fit a simple least-squares trend; return daily future demand estimates."""
    if not points:
        return []
    n = len(points)
    x_mean = (n - 1) / 2
    y_mean = sum(points) / n
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    slope = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(points)) / denominator if denominator else 0
    intercept = y_mean - slope * x_mean
    return [max(0, round(intercept + slope * (n + step), 1)) for step in range(horizon)]


def moving_average_forecast(points, horizon=14, window=7):
    """Return a naive moving average forecast."""
    if not points:
        return [0.0] * horizon
    avg = sum(points[-window:]) / min(len(points), window)
    return [round(max(0, avg), 1)] * horizon


def select_best_forecast(points, horizon=14):
    """Select between MA, Linear, and Holt-Winters by evaluating holdout MAE."""
    if len(points) < 8:
        return "Moving Average", moving_average_forecast(points, horizon), 0.0
        
    holdout_len = min(7, len(points) // 4)
    train = points[:-holdout_len]
    actual = points[-holdout_len:]
    
    candidates = {}
    candidates["Moving Average"] = moving_average_forecast(train, holdout_len)
    if len(train) >= 2:
        candidates["Linear Trend"] = linear_forecast(train, holdout_len)
    if len(train) >= 14:
        candidates["Holt-Winters (Exponential)"] = holt_winters_forecast(train, holdout_len)
        
    best_model = "Moving Average"
    best_mae = float('inf')
    
    for name, preds in candidates.items():
        mae = sum(abs(a - p) for a, p in zip(actual, preds)) / holdout_len
        if mae < best_mae:
            best_mae = mae
            best_model = name
            
    if best_model == "Holt-Winters (Exponential)":
        future = holt_winters_forecast(points, horizon)
    elif best_model == "Linear Trend":
        future = linear_forecast(points, horizon)
    else:
        future = moving_average_forecast(points, horizon)
        
    return best_model, future, best_mae


def analysis_for_sales(rows, horizon=14, demand_multiplier=1.0):
    grouped = defaultdict(list)
    inventory = {}
    for row in rows:
        grouped[row["product"]].append(row)
        if row.get("inventory") is not None:
            inventory[row["product"]] = row["inventory"]
    products = []
    for product, records in grouped.items():
        ordered = sorted(records, key=lambda item: item["date"])
        by_day = defaultdict(float)
        for item in ordered:
            by_day[item["date"]] += item["quantity"] * demand_multiplier
        start = datetime.fromisoformat(ordered[0]["date"]).date()
        end = datetime.fromisoformat(ordered[-1]["date"]).date()
        series = [by_day[(start + timedelta(days=i)).isoformat()] for i in range((end - start).days + 1)]
        
        best_model, forecast, best_mae = select_best_forecast(series, horizon)
        
        daily_average = round(sum(series) / len(series), 1)
        forecast_total = round(sum(forecast), 1)
        on_hand = inventory.get(product)
        days_cover = round(on_hand / daily_average, 1) if on_hand is not None and daily_average else None
        reorder = on_hand is not None and on_hand < forecast_total
        products.append({
            "product": product,
            "total_sales": round(sum(series), 1),
            "daily_average": daily_average,
            "latest_inventory": on_hand,
            "days_cover": days_cover,
            "forecast_14d": forecast_total,
            "reorder": reorder,
            "forecast_model": best_model,
            "model_mae": round(best_mae, 1),
            "forecast": forecast,
            "history": [{"date": date, "sales": round(value, 1)} for date, value in sorted(by_day.items())],
        })
    return sorted(products, key=lambda item: item["total_sales"], reverse=True)


def anomalies(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["product"]].append(row)
    findings = []
    for product, records in grouped.items():
        values = [item["quantity"] for item in records]
        if len(values) < 5:
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        stddev = math.sqrt(variance)
        if not stddev:
            continue
        for record in records:
            z_score = abs(record["quantity"] - mean) / stddev
            if z_score >= 2:
                findings.append({"product": product, "date": record["date"], "sales": record["quantity"], "z_score": round(z_score, 1)})
    return sorted(findings, key=lambda item: item["z_score"], reverse=True)[:8]


def chunk_text(text, target_size=700, overlap_sentences=1):
    """Split text into chunks on sentence boundaries with optional sentence overlap."""
    clean = re.sub(r'\s+', ' ', text).strip()
    if not clean:
        return []
    # Split on sentence boundaries (period/exclamation/question followed by space or end)
    sentences = re.split(r'(?<=[.!?])\s+', clean)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [clean] if clean else []
    chunks = []
    current_sentences = []
    current_length = 0
    for sentence in sentences:
        sentence_length = len(sentence)
        # If adding this sentence exceeds target and we have content, finalize chunk
        if current_length + sentence_length > target_size and current_sentences:
            chunks.append(' '.join(current_sentences))
            # Keep last N sentences for overlap
            current_sentences = current_sentences[-overlap_sentences:] if overlap_sentences else []
            current_length = sum(len(s) for s in current_sentences) + len(current_sentences) - 1 if current_sentences else 0
        current_sentences.append(sentence)
        current_length += sentence_length + (1 if current_length else 0)
    # Don't forget the last chunk
    if current_sentences:
        last_chunk = ' '.join(current_sentences)
        # Avoid duplicating if identical to previous chunk
        if not chunks or last_chunk != chunks[-1]:
            chunks.append(last_chunk)
    return chunks


def rank_chunks(query, chunks, limit=4):
    base_terms, expanded_terms = query_terms(query)
    if not base_terms:
        return []
    query_tokens = Counter(expanded_terms)
    ranked = []
    for chunk in chunks:
        doc_tokens = Counter(tokens(chunk["content"]))
        overlap = sum(min(count, doc_tokens[token]) for token, count in query_tokens.items())
        if overlap:
            # Exact question terms matter much more than loose business synonyms.
            exact_overlap = sum(min(1, doc_tokens[token]) for token in base_terms)
            phrase_bonus = 2 if " ".join(base_terms) in chunk["content"].lower() else 0
            # Normalization prevents long documents from always winning.
            score = (overlap + exact_overlap * 2 + phrase_bonus) / math.sqrt(sum(doc_tokens.values()) or 1)
            ranked.append({**chunk, "score": round(score, 3)})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]


def fallback_answer(question, sources):
    if not sources:
        return "I could not find supporting information in the uploaded knowledge base. Upload a policy, supplier, or product document and try again."
    base_terms, expanded_terms = query_terms(question)
    candidates = []
    for source_index, source in enumerate(sources):
        sentences = re.split(r"(?<=[.!?])\s+", source["content"])
        for sentence_index, sentence in enumerate(sentences):
            sentence_terms = Counter(tokens(sentence))
            expanded_score = sum(min(1, sentence_terms[term]) for term in expanded_terms)
            exact_score = sum(min(1, sentence_terms[term]) for term in base_terms)
            if expanded_score:
                # Source rank is a small tie-breaker; the answer itself is sentence-specific.
                score = exact_score * 3 + expanded_score + (len(sources) - source_index) * 0.1
                candidates.append((score, source_index, sentence_index, sentence.strip()))
    candidates.sort(key=lambda item: item[:3], reverse=True)
    selected = []
    # Prefer one precise answer over appending a plausible but unrelated product fact.
    # Broader comparison questions can intentionally return a second matching sentence.
    allow_second = any(term in tokens(question) for term in ("all", "compare", "each", "every"))
    best_source = candidates[0][1] if candidates else None
    for _, source_index, _, sentence in candidates:
        if source_index == best_source and sentence and sentence not in selected:
            selected.append(sentence)
        if len(selected) >= (2 if allow_second else 1):
            break
    if not selected:
        selected = [sources[0]["content"][:450].strip()]
    return " ".join(selected)
