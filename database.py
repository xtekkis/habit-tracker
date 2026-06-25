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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at DATE DEFAULT (DATE('now'))
        )
    """)

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE logs ADD COLUMN notes TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()

def get_habit(habit_id):
    conn = get_connection()
    habit = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    conn.close()
    return habit

def get_logs_for_month(habit_id, year, month):
    conn = get_connection()
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year+1:04d}-01-01"
    else:
        end = f"{year:04d}-{month+1:02d}-01"
    rows = conn.execute(
        "SELECT logged_date, notes FROM logs WHERE habit_id = ? AND logged_date >= ? AND logged_date < ?",
        (habit_id, start, end)
    ).fetchall()
    conn.close()
    return {row["logged_date"]: row["notes"] for row in rows}

def get_todays_notes():
    conn = get_connection()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT habit_id, notes FROM logs WHERE logged_date = ? AND notes IS NOT NULL AND notes != ''",
        (today,)
    ).fetchall()
    conn.close()
    return {row["habit_id"]: row["notes"] for row in rows}

def get_categories():
    conn = get_connection()
    cats = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    conn.close()
    return cats

def get_weekly_counts():
    conn = get_connection()
    today = date.today()
    week_ago = today - timedelta(days=6)
    rows = conn.execute(
        "SELECT habit_id, COUNT(*) as count FROM logs WHERE logged_date BETWEEN ? AND ? GROUP BY habit_id",
        (week_ago.isoformat(), today.isoformat())
    ).fetchall()
    conn.close()
    return {row["habit_id"]: row["count"] for row in rows}

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

def get_monthly_summary():
    conn = get_connection()
    today = date.today()
    month_start = today.replace(day=1)
    days_in_month = (today - month_start).days + 1

    habits = conn.execute("SELECT * FROM habits ORDER BY created_at DESC").fetchall()
    summary = []
    for habit in habits:
        count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE habit_id = ? AND logged_date BETWEEN ? AND ?",
            (habit["id"], month_start.isoformat(), today.isoformat())
        ).fetchone()[0]
        summary.append({"name": habit["name"], "count": count, "out_of": days_in_month})
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