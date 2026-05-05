import sqlite3
from config import DATABASE_PATH


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name_th TEXT NOT NULL,
            name_en TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ingredients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name_th     TEXT NOT NULL,
            name_en     TEXT NOT NULL,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            unit        TEXT NOT NULL DEFAULT 'kg',
            par_level   REAL NOT NULL DEFAULT 0,
            min_level   REAL NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS stock_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
            current_qty   REAL NOT NULL,
            estimate_buy  REAL NOT NULL DEFAULT 0,
            note          TEXT,
            created_by    TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS purchase_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS purchase_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id    INTEGER NOT NULL REFERENCES purchase_requests(id),
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
            need_qty      REAL NOT NULL,
            suggest_qty   REAL NOT NULL
        );
    """)

    # Seed categories if empty
    existing = c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing == 0:
        c.executemany(
            "INSERT INTO categories (name_th, name_en) VALUES (?, ?)",
            [
                ("เนื้อสัตว์", "Meat"),
                ("ผัก", "Vegetables"),
                ("เครื่องปรุง", "Seasonings"),
                ("เส้น/แป้ง", "Noodles & Flour"),
                ("อื่นๆ", "Others"),
            ],
        )

        # Seed sample ingredients
        sample = [
            ("หมู", "Pork", 1, "kg", 50, 20),
            ("ไก่", "Chicken", 1, "kg", 40, 15),
            ("กุ้ง", "Shrimp", 1, "kg", 20, 8),
            ("กะทิ", "Coconut Milk", 5, "ลิตร", 30, 10),
            ("พริกแกงข้าวซอย", "Khao Soi Curry Paste", 3, "kg", 10, 3),
            ("เส้นข้าวซอย", "Khao Soi Noodles", 4, "kg", 20, 8),
            ("หัวหอม", "Onion", 2, "kg", 15, 5),
            ("กระเทียม", "Garlic", 2, "kg", 10, 3),
            ("น้ำมัน", "Oil", 3, "ลิตร", 20, 5),
            ("น้ำปลา", "Fish Sauce", 3, "ลิตร", 10, 3),
        ]
        c.executemany(
            """INSERT INTO ingredients
               (name_th, name_en, category_id, unit, par_level, min_level)
               VALUES (?, ?, ?, ?, ?, ?)""",
            sample,
        )

    conn.commit()
    conn.close()
