"""Deterministic demo data used by the one-click product tour."""
from datetime import date, timedelta


def demo_sales_rows():
    products = {
        "Wireless Headphones": (22, 68, 0.22),
        "Mechanical Keyboard": (14, 38, 0.14),
        "USB-C Hub": (18, 24, 0.08),
        "Webcam Pro": (9, 17, 0.17),
    }
    today = date.today()
    rows = []
    for index in range(60):
        day = today - timedelta(days=59 - index)
        for product, (base, stock, trend) in products.items():
            weekly = (index % 7) * 0.6
            quantity = round(base + weekly + (index * trend) + ((index * len(product)) % 5) - 2, 1)
            if product == "USB-C Hub" and index == 47:
                quantity = 55
            rows.append({"date": day.isoformat(), "product": product, "quantity": max(quantity, 1), "inventory": stock if index == 59 else None})
    return rows


DEMO_DOCUMENTS = [
    ("Supplier lead times", """Acme Electronics supplier policy. Wireless Headphones have a standard lead time of 12 calendar days and a minimum order quantity of 50 units. Mechanical Keyboard lead time is 18 days with a minimum order of 25 units. USB-C Hub lead time is 9 days with a minimum order of 40 units. Webcam Pro lead time is 15 days with a minimum order of 20 units. Orders submitted before 2 PM are processed on the same business day."""),
    ("Replenishment policy", """Inventory replenishment policy: raise a reorder recommendation when forecast demand during supplier lead time plus a seven-day safety buffer exceeds inventory on hand. Priority is high when projected stock cover is below seven days. Inventory recommendations are planning signals; the operations manager must approve every purchase order."""),
    ("Product notes", """Wireless Headphones are the highest-margin audio product and typically sell more during weekend promotion periods. USB-C Hub demand often rises alongside laptop sales. Webcam Pro is used by business customers and is less sensitive to consumer discount campaigns."""),
]

