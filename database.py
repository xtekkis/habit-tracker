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

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN repeat_days TEXT NOT NULL DEFAULT '1111111'")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN reminder_time TEXT DEFAULT NULL")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN icon TEXT DEFAULT 'check'")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN color TEXT DEFAULT '#D96A34'")
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO preferences (key, value) VALUES ('show_streaks', '1')")
    cursor.execute("INSERT OR IGNORE INTO preferences (key, value) VALUES ('start_week', 'monday')")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_state (
            id INTEGER PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO player_state (id, xp, level) VALUES (1, 0, 1)")

    conn.commit()
    conn.close()

def get_preferences():
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    conn.close()
    prefs = {row["key"]: row["value"] for row in rows}
    prefs.setdefault("show_streaks", "1")
    prefs.setdefault("start_week", "monday")
    return prefs

def set_preference(key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO preferences (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()

def get_week_start(today, start_week):
    if start_week == "sunday":
        return today - timedelta(days=(today.weekday() + 1) % 7)
    return today - timedelta(days=today.weekday())

def count_perfect_days(start, end, habit_count):
    if habit_count == 0:
        return 0
    conn = get_connection()
    result = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT logged_date
            FROM logs
            WHERE logged_date BETWEEN ? AND ?
            GROUP BY logged_date
            HAVING COUNT(DISTINCT habit_id) = ?
        )
    """, (start, end, habit_count)).fetchone()[0]
    conn.close()
    return result

def get_best_streak(habit_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT logged_date FROM logs WHERE habit_id = ? ORDER BY logged_date ASC",
        (habit_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return 0

    dates = sorted(date.fromisoformat(row["logged_date"]) for row in rows)
    best = current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
            best = max(best, current)
        elif (dates[i] - dates[i - 1]).days > 1:
            current = 1
    return best

def get_recent_notes(habit_id, limit=8):
    conn = get_connection()
    rows = conn.execute(
        "SELECT logged_date, notes FROM logs WHERE habit_id = ? AND notes IS NOT NULL AND notes != '' ORDER BY logged_date DESC LIMIT ?",
        (habit_id, limit)
    ).fetchall()
    conn.close()
    return [{"date": row["logged_date"], "notes": row["notes"]} for row in rows]

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

def get_weekly_counts(start_week="monday"):
    conn = get_connection()
    today = date.today()
    week_start = get_week_start(today, start_week)
    rows = conn.execute(
        "SELECT habit_id, COUNT(*) as count FROM logs WHERE logged_date BETWEEN ? AND ? GROUP BY habit_id",
        (week_start.isoformat(), today.isoformat())
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

def xp_for_level(level):
    return 100 + (level - 1) * 50

def rank_for_level(level):
    if level >= 100: return "Evergreen"
    if level >= 91:  return "Elder of the Grove"
    if level >= 81:  return "Forest Guardian"
    if level >= 71:  return "Ancient Oak"
    if level >= 61:  return "Elder Oak"
    if level >= 51:  return "Oak"
    if level >= 41:  return "Young Tree"
    if level >= 31:  return "Budding Tree"
    if level >= 21:  return "Sapling"
    if level >= 11:  return "Sprout"
    return "Seed"

def get_player_state():
    conn = get_connection()
    row = conn.execute("SELECT xp, level FROM player_state WHERE id = 1").fetchone()
    conn.close()
    level = row["level"]
    xp = row["xp"]
    needed = xp_for_level(level)
    return {
        "xp": xp,
        "level": level,
        "xp_needed": needed,
        "pct": int(xp / needed * 100),
        "rank": rank_for_level(level),
    }

def add_xp(amount):
    conn = get_connection()
    row = conn.execute("SELECT xp, level FROM player_state WHERE id = 1").fetchone()
    xp = row["xp"] + amount
    level = row["level"]
    needed = xp_for_level(level)
    while xp >= needed:
        xp -= needed
        level += 1
        needed = xp_for_level(level)
    conn.execute("UPDATE player_state SET xp = ?, level = ? WHERE id = 1", (xp, level))
    conn.commit()
    conn.close()

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