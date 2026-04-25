"""In-memory mock store for the customer-support example.

12 customers × 30 orders. Plenty of states (shipped / processing /
delivered / cancelled / refunded) so the agent has something to filter
and reason over. Numbers are stable across runs because we generate
deterministically — no `random` / `faker`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

CUSTOMERS: dict[str, dict[str, Any]] = {
    "cust_1":  {"id": "cust_1",  "name": "Alice Example",   "email": "alice@example.com",   "tier": "gold"},
    "cust_2":  {"id": "cust_2",  "name": "Bob Example",     "email": "bob@example.com",     "tier": "silver"},
    "cust_3":  {"id": "cust_3",  "name": "Carol Foster",    "email": "carol@example.com",   "tier": "bronze"},
    "cust_4":  {"id": "cust_4",  "name": "Dan Powell",      "email": "dan@example.com",     "tier": "gold"},
    "cust_5":  {"id": "cust_5",  "name": "Eve Tanaka",      "email": "eve@example.com",     "tier": "silver"},
    "cust_6":  {"id": "cust_6",  "name": "Frank Garcia",    "email": "frank@example.com",   "tier": "bronze"},
    "cust_7":  {"id": "cust_7",  "name": "Grace Liu",       "email": "grace@example.com",   "tier": "gold"},
    "cust_8":  {"id": "cust_8",  "name": "Henry Okafor",    "email": "henry@example.com",   "tier": "silver"},
    "cust_9":  {"id": "cust_9",  "name": "Ines Romero",     "email": "ines@example.com",    "tier": "bronze"},
    "cust_10": {"id": "cust_10", "name": "Jack Petrov",     "email": "jack@example.com",    "tier": "gold"},
    "cust_11": {"id": "cust_11", "name": "Kira Nilsson",    "email": "kira@example.com",    "tier": "silver"},
    "cust_12": {"id": "cust_12", "name": "Liam O'Brien",    "email": "liam@example.com",    "tier": "bronze"},
}


def _seed_orders() -> dict[str, dict[str, Any]]:
    today = datetime(2026, 4, 25)
    rows: list[tuple[str, str, str, float, str, int]] = [
        # (order_id, customer_id, status, total, item, days_ago)
        ("123",  "cust_1",  "shipped",    99.99,  "Mechanical keyboard",        2),
        ("124",  "cust_1",  "delivered",  15.49,  "USB-C cable",                12),
        ("125",  "cust_1",  "processing", 249.00, "Standing desk converter",    1),
        ("126",  "cust_2",  "delivered",  45.00,  "Wireless mouse",             18),
        ("127",  "cust_2",  "cancelled",  19.99,  "Phone stand",                25),
        ("128",  "cust_2",  "shipped",    199.50, "Noise-cancelling headphones", 3),
        ("129",  "cust_3",  "delivered",  12.99,  "Notebook",                   30),
        ("130",  "cust_3",  "refunded",   89.00,  "Ergonomic mousepad",         8),
        ("131",  "cust_4",  "shipped",    349.00, "Office chair cushion",       4),
        ("132",  "cust_4",  "processing", 22.50,  "USB hub",                    1),
        ("133",  "cust_4",  "delivered",  149.00, "Bluetooth speaker",          21),
        ("134",  "cust_5",  "delivered",  29.95,  "Webcam",                     14),
        ("135",  "cust_5",  "shipped",    78.40,  "External SSD 1TB",           5),
        ("136",  "cust_6",  "processing", 9.99,   "Cable organiser",            1),
        ("137",  "cust_6",  "delivered",  399.00, "Monitor 27in",               45),
        ("138",  "cust_7",  "shipped",    1299.00,"Standing desk frame",        6),
        ("139",  "cust_7",  "delivered",  64.00,  "Desk lamp",                  19),
        ("140",  "cust_7",  "processing", 18.50,  "HDMI 2.1 cable",             1),
        ("141",  "cust_8",  "cancelled",  120.00, "Mesh router",                10),
        ("142",  "cust_8",  "delivered",  35.00,  "Coffee mug warmer",          22),
        ("143",  "cust_8",  "shipped",    52.30,  "Whiteboard markers (24)",    7),
        ("144",  "cust_9",  "delivered",  17.99,  "Sticky notes pack",          28),
        ("145",  "cust_9",  "refunded",   210.00, "Webcam ring light",          15),
        ("146",  "cust_10", "shipped",    899.00, "Mechanical keyboard premium",4),
        ("147",  "cust_10", "delivered",  44.50,  "Wrist rest",                 17),
        ("148",  "cust_11", "processing", 79.00,  "Document camera",            1),
        ("149",  "cust_11", "delivered",  31.20,  "Power strip",                26),
        ("150",  "cust_12", "shipped",    14.99,  "USB extension",              5),
        ("151",  "cust_12", "delivered",  450.00, "Office chair",               33),
        ("152",  "cust_12", "processing", 8.99,   "Pen set",                    1),
    ]
    out: dict[str, dict[str, Any]] = {}
    for order_id, customer_id, status, total, item, days_ago in rows:
        created = (today - timedelta(days=days_ago)).date().isoformat()
        out[order_id] = {
            "id": order_id,
            "customer_id": customer_id,
            "status": status,
            "total": total,
            "item": item,
            "created_at": created,
        }
    return out


ORDERS: dict[str, dict[str, Any]] = _seed_orders()
