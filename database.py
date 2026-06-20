import sqlite3
from datetime import date, timedelta

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

def get_weekly_summary():
    conn = get_connection()
    today = date.today()
    week_ago = today - timedelta(days=6)

    habits = conn.execute("SELECT * FROM habits ORDER BY created_at DESC").fetchall()
    summary = []
    for habit in habits:
        count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE habit_id = ? AND logged_date BETWEEN ? AND ?",
            (habit["id"], week_ago.isoformat(), today.isoformat())
        ).fetchone()[0]
        summary.append({"name": habit["name"], "count": count, "out_of": 7})
    conn.close()
    return summary

def get_streak(habit_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT logged_date FROM logs WHERE habit_id = ? ORDER BY logged_date DESC",
        (habit_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return 0

    dates = set(date.fromisoformat(row["logged_date"]) for row in rows)
    today = date.today()
    check = today if today in dates else today - timedelta(days=1)

    if check not in dates:
        return 0

    streak = 0
    while check in dates:
        streak += 1
        check -= timedelta(days=1)

    return streak