"""Pure analysis and retrieval helpers for SmartStock AI."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
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
    """Accept the two most common spreadsheet date formats."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    raise ValueError("Date must be YYYY-MM-DD, DD/MM/YYYY, or MM/DD/YYYY")


def normalise_sales_rows(rows):
    """Return validated date/product/quantity records from a CSV DictReader."""
    output = []
    aliases = {
        "date": ("date", "order_date", "sales_date"),
        "product": ("product", "product_name", "item", "sku"),
        "quantity": ("quantity", "sales", "sales_quantity", "units_sold", "qty"),
        "inventory": ("inventory", "stock", "stock_on_hand", "on_hand"),
    }
    for raw in rows:
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        def field(name):
            return next((row[key] for key in aliases[name] if row.get(key)), "")
        date = parse_date(field("date"))
        product = field("product")
        if not product:
            raise ValueError("Every row needs a Product column")
        try:
            quantity = float(field("quantity"))
        except ValueError:
            raise ValueError("Every row needs a numeric Quantity/Sales column")
        inventory_value = field("inventory")
        try:
            inventory = float(inventory_value) if inventory_value else None
        except ValueError:
            inventory = None
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


def analysis_for_sales(rows, horizon=14):
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
            by_day[item["date"]] += item["quantity"]
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
