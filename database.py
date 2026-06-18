import sqlite3

DB_NAME = "habits.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at DATE DEFAULT (DATE('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            logged_date DATE DEFAULT (DATE('now')),
            FOREIGN KEY (habit_id) REFERENCES habits(id),
            UNIQUE(habit_id, logged_date)
        )
    """)

    conn.commit()
    conn.close()