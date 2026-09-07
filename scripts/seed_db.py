"""Seed a deterministic SQLite database used by the QueryMind baseline.

Running this script twice always produces byte-identical data, so ground-truth
answers quoted in the proposal stay valid for anyone who reproduces the run.
"""

import argparse
import os
import random
import sqlite3
from datetime import date, timedelta

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "analytics.db"
)

RANDOM_SEED = 598

CUSTOMERS = [
    ("Alice Johnson", "West"),
    ("Bob Smith", "East"),
    ("Charlie Davis", "West"),
    ("Diana Prince", "Midwest"),
    ("Ethan Hunt", "South"),
    ("Fiona Gallagher", "East"),
    ("George Martin", "West"),
    ("Hannah Lee", "South"),
    ("Ivan Petrov", "Midwest"),
    ("Julia Chen", "West"),
    ("Kevin Brown", "East"),
    ("Laura Wilson", "South"),
    ("Mohan Rao", "Midwest"),
    ("Nina Patel", "West"),
    ("Oscar Diaz", "East"),
    ("Priya Sharma", "South"),
    ("Quentin Cole", "Midwest"),
    ("Rachel Adams", "West"),
    ("Samuel Osei", "East"),
    ("Tara Nguyen", "South"),
]

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 24.99),
    ("Mechanical Keyboard", "Electronics", 89.50),
    ("27in Monitor", "Electronics", 279.00),
    ("USB-C Hub", "Electronics", 45.00),
    ("Noise Cancelling Headphones", "Electronics", 199.99),
    ("Standing Desk", "Furniture", 420.00),
    ("Ergonomic Chair", "Furniture", 350.00),
    ("Desk Lamp", "Furniture", 39.99),
    ("Bookshelf", "Furniture", 129.00),
    ("Filing Cabinet", "Furniture", 175.50),
    ("Notebook Pack", "Stationery", 12.75),
    ("Gel Pen Set", "Stationery", 8.99),
    ("Whiteboard", "Stationery", 65.00),
    ("Sticky Notes Bulk", "Stationery", 15.25),
    ("Printer Paper Ream", "Stationery", 9.50),
]

SCHEMA = """
CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    customer_name TEXT    NOT NULL,
    region        TEXT    NOT NULL,
    signup_date   TEXT    NOT NULL
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    product_name TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    unit_price   REAL    NOT NULL
);

CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
    product_id   INTEGER NOT NULL REFERENCES products(product_id),
    quantity     INTEGER NOT NULL,
    total_amount REAL    NOT NULL,
    order_date   TEXT    NOT NULL
);
"""


def build(db_path: str, n_orders: int = 300) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    rng = random.Random(RANDOM_SEED)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    start = date(2024, 1, 1)

    customer_rows = [
        (
            i + 1,
            name,
            region,
            (start + timedelta(days=rng.randint(0, 120))).isoformat(),
        )
        for i, (name, region) in enumerate(CUSTOMERS)
    ]
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", customer_rows)

    product_rows = [
        (i + 1, name, category, price)
        for i, (name, category, price) in enumerate(PRODUCTS)
    ]
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", product_rows)

    order_rows = []
    for order_id in range(1, n_orders + 1):
        customer_id = rng.randint(1, len(CUSTOMERS))
        product_id = rng.randint(1, len(PRODUCTS))
        quantity = rng.randint(1, 6)
        unit_price = PRODUCTS[product_id - 1][2]
        total = round(unit_price * quantity, 2)
        order_date = (start + timedelta(days=rng.randint(0, 364))).isoformat()
        order_rows.append(
            (order_id, customer_id, product_id, quantity, total, order_date)
        )
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", order_rows)

    conn.commit()

    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("customers", "products", "orders")
    }
    conn.close()

    print(f"[OK] Seeded database at {db_path}")
    for table, count in counts.items():
        print(f"     {table}: {count} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the QueryMind demo database.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to the SQLite file.")
    parser.add_argument("--orders", type=int, default=300, help="Number of orders.")
    args = parser.parse_args()
    build(args.db, args.orders)


if __name__ == "__main__":
    main()
