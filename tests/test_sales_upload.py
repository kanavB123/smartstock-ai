import pytest

from app.services import infer_sales_column_mapping, normalise_sales_rows, parse_sales_csv_text


def test_normalise_sales_rows_accepts_common_aliases():
    rows = normalise_sales_rows([
        {"Order Date": "01/09/2026", "item": "Widget", "Sales Qty": "12", "Stock On Hand": "25"},
        {"Date": "2026-09-02", "Product": "Widget", "Quantity": "8", "Inventory": "20"},
    ])

    assert len(rows) == 2
    assert rows[0]["date"] == "2026-09-01"
    assert rows[0]["product"] == "Widget"
    assert rows[0]["quantity"] == 12.0
    assert rows[0]["inventory"] == 25.0
    assert rows[1]["date"] == "2026-09-02"


def test_parse_sales_csv_text_accepts_semicolon_delimited_columns():
    text = """order date;product;units sold;stock on hand;notes
01/09/2026;Widget;12;25;promo
2026/09/02;Gadget;8;15;new"""

    rows = parse_sales_csv_text(text)

    assert len(rows) == 2
    assert rows[0]["product"] == "Widget"
    assert rows[0]["quantity"] == 12.0
    assert rows[0]["inventory"] == 25.0
    assert rows[1]["product"] == "Gadget"


def test_parse_sales_csv_text_maps_near_match_headers_and_formatted_numbers():
    text = """Transacton Date,Product Description,Units Purchased,Closing Stock
2026-09-01,Desk Lamp,"1,250","₹ 2,400"
,,,
"""

    rows = parse_sales_csv_text(text)

    assert rows == [{"date": "2026-09-01", "product": "Desk Lamp", "quantity": 1250.0, "inventory": 2400.0}]


def test_infer_sales_column_mapping_does_not_guess_ambiguous_headers():
    mapping = infer_sales_column_mapping(["Date", "Product", "Sales", "Sales Quantity"])

    assert mapping == {"date": "Date", "product": "Product"}


def test_units_header_is_accepted_when_it_is_the_only_quantity_column():
    rows = parse_sales_csv_text("Date,Product,Units\n2026-09-01,Desk Lamp,12")

    assert rows[0]["quantity"] == 12.0


def test_invalid_quantity_error_names_row_and_value():
    with pytest.raises(ValueError, match="CSV row 2.*received 'many'"):
        parse_sales_csv_text("Date,Product,Quantity\n2026-09-01,Desk Lamp,many")
