"""Analytics, recommendations, and report utilities for the capstone edition."""
from collections import defaultdict
from datetime import date
from io import BytesIO
import math

from app.services import linear_forecast, moving_average_forecast, holt_winters_forecast

def _series_by_product(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["product"]].append(row)
    series = {}
    for product, records in grouped.items():
        ordered = sorted(records, key=lambda item: item["date"])
        series[product] = [item["quantity"] for item in ordered]
    return series


def _error(actual, predicted):
    if not actual:
        return {"mae": 0, "rmse": 0, "samples": 0}
    differences = [a - p for a, p in zip(actual, predicted)]
    return {
        "mae": round(sum(abs(value) for value in differences) / len(differences), 2),
        "rmse": round(math.sqrt(sum(value ** 2 for value in differences) / len(differences)), 2),
        "samples": len(actual),
    }


def forecast_accuracy(rows):
    """Transparent back-test comparing all models on a holdout set."""
    all_actual, all_ma, all_linear, all_hw = [], [], [], []
    for values in _series_by_product(rows).values():
        holdout = min(14, max(2, len(values) // 4))
        if len(values) < holdout + 4:
            continue
        train, actual = values[:-holdout], values[-holdout:]
        
        all_actual.extend(actual)
        all_ma.extend(moving_average_forecast(train, holdout))
        all_linear.extend(linear_forecast(train, holdout) if len(train) >= 2 else moving_average_forecast(train, holdout))
        all_hw.extend(holt_winters_forecast(train, holdout) if len(train) >= 14 else moving_average_forecast(train, holdout))
        
    models = [
        {"name": "Holt-Winters (Exponential)", **_error(all_actual, all_hw)},
        {"name": "Linear Trend", **_error(all_actual, all_linear)},
        {"name": "Moving Average", **_error(all_actual, all_ma)},
    ]
    models.sort(key=lambda m: m["mae"])
    models[0]["selected"] = True
    for m in models[1:]:
        m["selected"] = False
        
    return {
        "models": models,
        "note": "Back-test holds out recent observations and compares predicted demand with actual sales.",
    }


def inventory_recommendations(products, lead_time_days=14, safety_days=7, anomalies_list=None):
    if anomalies_list is None:
        anomalies_list = []
    recommendations = []
    for product in products:
        inventory = product["latest_inventory"]
        if inventory is None or product["daily_average"] <= 0:
            continue
        daily = product["daily_average"]
        safety_stock = math.ceil(daily * safety_days)
        target_stock = math.ceil(daily * lead_time_days + safety_stock)
        quantity = max(0, math.ceil(target_stock - inventory))
        stockout_days = round(inventory / daily, 1)
        
        reasons = []
        if stockout_days < lead_time_days:
            priority = "Critical"
            reasons.append(f"Lead time risk: Projected stockout in {stockout_days} days is shorter than the {lead_time_days}-day supplier lead time.")
        elif quantity:
            priority = "High"
        else:
            priority = "On track"
            
        if inventory < safety_stock:
            reasons.append(f"Safety stock breach: Current inventory ({inventory}) is below the {safety_days}-day safety buffer ({safety_stock} units).")
        elif quantity > 0 and priority != "Critical":
            reasons.append(f"Below target: Inventory ({inventory}) has fallen below the target stock level ({target_stock} units).")
            
        prod_anomalies = [a for a in anomalies_list if a["product"] == product["product"]]
        if prod_anomalies:
            worst = max(prod_anomalies, key=lambda x: x["z_score"])
            reasons.append(f"Demand spike contribution: Unusual demand ({worst['sales']} units on {worst['date']}, {worst['z_score']}σ above normal) is accelerating depletion.")
            
        if not reasons and priority == "On track":
            reasons.append("Inventory levels are sufficient to cover forecasted demand and safety stock.")

        recommendations.append({
            "product": product["product"], "priority": priority, "inventory": inventory,
            "daily_demand": daily, "lead_time_days": lead_time_days, "safety_stock": safety_stock,
            "target_stock": target_stock, "recommended_order": quantity,
            "estimated_stockout_days": stockout_days,
            "action": "Raise purchase order today" if priority == "Critical" else ("Plan replenishment" if quantity else "Monitor"),
            "reasons": reasons,
        })
    priority_order = {"Critical": 0, "High": 1, "On track": 2}
    return sorted(recommendations, key=lambda item: (priority_order[item["priority"]], -item["recommended_order"]))


def operational_alerts(products, recommendation_rows, anomalies):
    alerts = []
    for recommendation in recommendation_rows:
        if recommendation["priority"] in ("Critical", "High"):
            alerts.append({
                "type": "stock", "severity": recommendation["priority"].lower(),
                "title": "{} needs replenishment".format(recommendation["product"]),
                "detail": "{} units recommended; projected stockout in {} days.".format(recommendation["recommended_order"], recommendation["estimated_stockout_days"]),
            })
    for anomaly in anomalies[:3]:
        alerts.append({
            "type": "demand", "severity": "watch", "title": "Unusual demand for {}".format(anomaly["product"]),
            "detail": "{} units on {} ({} standard deviations from normal).".format(anomaly["sales"], anomaly["date"], anomaly["z_score"]),
        })
    return alerts[:8]


def executive_report_pdf(summary, products, recommendations, alerts):
    """Create a compact, printable weekly operations briefing."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph, Table, TableStyle

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandTitle", parent=styles["Title"], textColor=colors.HexColor("#123D31"), fontName="Helvetica-Bold", fontSize=24, leading=30, spaceAfter=5))
    styles.add(ParagraphStyle(name="Muted", parent=styles["BodyText"], textColor=colors.HexColor("#65736B"), fontSize=9, leading=14))
    story = [Paragraph("SmartStock AI", styles["BrandTitle"]), Paragraph("Executive inventory briefing - {}".format(date.today().isoformat()), styles["Muted"]), Spacer(1, 10 * mm)]
    story.append(Paragraph("Operations snapshot", styles["Heading2"]))
    metrics = [["Units sold", "Active products", "Reorder signals", "Knowledge sources"], [str(summary["total_sales"]), str(summary["products"]), str(summary["reorder_alerts"]), str(summary["documents"])]]
    table = Table(metrics, colWidths=[42 * mm] * 4)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2F5E8")), ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#123D31")), ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDE5DE")), ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 9)]))
    story.extend([table, Spacer(1, 8 * mm), Paragraph("Priority recommendations", styles["Heading2"])])
    rec_data = [["Product", "Priority", "Order", "Stockout", "Action"]] + [[row["product"], row["priority"], str(row["recommended_order"]), "{} days".format(row["estimated_stockout_days"]), row["action"]] for row in recommendations[:6]]
    rec_table = Table(rec_data, colWidths=[42 * mm, 25 * mm, 22 * mm, 30 * mm, 48 * mm], repeatRows=1)
    rec_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123D31")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDE5DE")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.extend([rec_table, Spacer(1, 8 * mm), Paragraph("Active alerts", styles["Heading2"])])
    if alerts:
        for alert in alerts[:5]:
            story.append(Paragraph("<b>{}</b> - {}".format(alert["title"], alert["detail"]), styles["Muted"]))
            story.append(Spacer(1, 2 * mm))
    else:
        story.append(Paragraph("No priority alerts at the time of generation.", styles["Muted"]))
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("Forecast note: recommendations use the selected demand forecast, a 14-day default lead time, and a 7-day safety-stock buffer. They are planning signals and require operations approval.", styles["Muted"]))
    document.build(story)
    return buffer.getvalue()

