import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta

DB_NAME = "habits.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def db_connection():
    """Like get_connection(), but guarantees the connection is closed even if
    an exception is raised partway through a multi-query route."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # WAL mode lets readers and a single writer proceed concurrently instead
    # of blocking each other, which the default rollback-journal mode doesn't.
    # This is a database-level setting persisted in the file, so it only needs
    # setting once here rather than on every connection.
    cursor.execute("PRAGMA journal_mode=WAL")

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

    try:
        cursor.execute("ALTER TABLE habits ADD COLUMN owner TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE categories ADD COLUMN owner TEXT")
    except Exception:
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_habits_owner ON habits(owner)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_categories_owner ON categories(owner)")

    # habits.name and categories.name were UNIQUE globally, from before multi-user
    # existed. SQLite can't alter that constraint in place, so the tables are
    # rebuilt with a per-owner UNIQUE(owner, name) instead - otherwise two
    # different browsers could never both have a habit named e.g. "Exercise".
    # Guarded by the presence of the new index, so this only runs once.
    cursor.execute("PRAGMA index_list(habits)")
    if not any(row[1] == "idx_habits_owner_name" for row in cursor.fetchall()):
        cursor.execute("ALTER TABLE habits RENAME TO habits_old")
        cursor.execute("""
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at DATE DEFAULT (DATE('now')),
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                repeat_days TEXT NOT NULL DEFAULT '1111111',
                reminder_time TEXT DEFAULT NULL,
                icon TEXT DEFAULT 'check',
                color TEXT DEFAULT '#D96A34',
                owner TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO habits (id, name, created_at, category_id, repeat_days, reminder_time, icon, color, owner)
            SELECT id, name, created_at, category_id, repeat_days, reminder_time, icon, color, owner FROM habits_old
        """)
        cursor.execute("DROP TABLE habits_old")
        cursor.execute("CREATE UNIQUE INDEX idx_habits_owner_name ON habits(owner, name)")
        cursor.execute("CREATE INDEX idx_habits_owner ON habits(owner)")

    cursor.execute("PRAGMA index_list(categories)")
    if not any(row[1] == "idx_categories_owner_name" for row in cursor.fetchall()):
        cursor.execute("ALTER TABLE categories RENAME TO categories_old")
        cursor.execute("""
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at DATE DEFAULT (DATE('now')),
                owner TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO categories (id, name, created_at, owner)
            SELECT id, name, created_at, owner FROM categories_old
        """)
        cursor.execute("DROP TABLE categories_old")
        cursor.execute("CREATE UNIQUE INDEX idx_categories_owner_name ON categories(owner, name)")
        cursor.execute("CREATE INDEX idx_categories_owner ON categories(owner)")

    # preferences/player_state predate multi-user and had a single global row
    # each (key alone as PK; a hardcoded id=1). Neither shape can hold one row
    # per browser, so - starting fresh, as already decided - they're rebuilt
    # below rather than migrated. Guarded so this only runs once.
    cursor.execute("PRAGMA table_info(preferences)")
    if not any(row[1] == "owner" for row in cursor.fetchall()):
        cursor.execute("DROP TABLE IF EXISTS preferences")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            owner TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (owner, key)
        )
    """)

    cursor.execute("PRAGMA table_info(player_state)")
    if not any(row[1] == "owner" for row in cursor.fetchall()):
        cursor.execute("DROP TABLE IF EXISTS player_state")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_state (
            owner TEXT PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Starting fresh for multi-user: wipe pre-multi-user habits/categories/logs
    # (rows with no owner). Safe to run on every startup: once real data has an
    # owner, this is a no-op, since new rows will always have one set.
    cursor.execute("DELETE FROM logs WHERE habit_id IN (SELECT id FROM habits WHERE owner IS NULL)")
    cursor.execute("DELETE FROM habits WHERE owner IS NULL")
    cursor.execute("DELETE FROM categories WHERE owner IS NULL")

    conn.commit()
    conn.close()

def get_preferences(owner, conn=None):
    owns_conn = conn is None
    conn = conn or get_connection()
    rows = conn.execute("SELECT key, value FROM preferences WHERE owner = ?", (owner,)).fetchall()
    if owns_conn:
        conn.close()
    prefs = {row["key"]: row["value"] for row in rows}
    prefs.setdefault("show_streaks", "1")
    prefs.setdefault("start_week", "monday")
    return prefs

def set_preference(owner, key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO preferences (owner, key, value) VALUES (?, ?, ?) ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value",
        (owner, key, value)
    )
    conn.commit()
    conn.close()

def get_week_start(today, start_week):
    if start_week == "sunday":
        return today - timedelta(days=(today.weekday() + 1) % 7)
    return today - timedelta(days=today.weekday())

def count_perfect_days(start, end, habit_count, owner):
    if habit_count == 0:
        return 0
    conn = get_connection()
    result = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT l.logged_date
            FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE l.logged_date BETWEEN ? AND ? AND h.owner = ?
            GROUP BY l.logged_date
            HAVING COUNT(DISTINCT l.habit_id) = ?
        )
    """, (start, end, owner, habit_count)).fetchone()[0]
    conn.close()
    return result

def get_best_streak(habit_id, repeat_days):
    conn = get_connection()
    rows = conn.execute(
        "SELECT logged_date FROM logs WHERE habit_id = ? ORDER BY logged_date ASC",
        (habit_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return 0

    dates = set(date.fromisoformat(row["logged_date"]) for row in rows)
    repeat_days = repeat_days or "1111111"
    day = min(dates)
    end = max(dates)
    best = current = 0
    while day <= end:
        if repeat_days[day.weekday()] == "1":
            if day in dates:
                current += 1
                best = max(best, current)
            else:
                current = 0
        day += timedelta(days=1)
    return best

def get_habit(habit_id, owner):
    conn = get_connection()
    habit = conn.execute("SELECT * FROM habits WHERE id = ? AND owner = ?", (habit_id, owner)).fetchone()
    conn.close()
    return habit

def category_belongs_to_owner(category_id, owner):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM categories WHERE id = ? AND owner = ?", (category_id, owner)).fetchone()
    conn.close()
    return row is not None

def get_logged_dates_for_month(habit_id, year, month):
    conn = get_connection()
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year+1:04d}-01-01"
    else:
        end = f"{year:04d}-{month+1:02d}-01"
    rows = conn.execute(
        "SELECT logged_date FROM logs WHERE habit_id = ? AND logged_date >= ? AND logged_date < ?",
        (habit_id, start, end)
    ).fetchall()
    conn.close()
    return [row["logged_date"] for row in rows]

def get_categories(owner, conn=None):
    owns_conn = conn is None
    conn = conn or get_connection()
    cats = conn.execute("SELECT * FROM categories WHERE owner = ? ORDER BY name", (owner,)).fetchall()
    if owns_conn:
        conn.close()
    return cats

def get_weekly_counts(owner, start_week="monday", today=None, conn=None):
    owns_conn = conn is None
    conn = conn or get_connection()
    today = today or date.today()
    week_start = get_week_start(today, start_week)
    rows = conn.execute("""
        SELECT l.habit_id, COUNT(*) as count FROM logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE l.logged_date BETWEEN ? AND ? AND h.owner = ?
        GROUP BY l.habit_id
    """, (week_start.isoformat(), today.isoformat(), owner)).fetchall()
    if owns_conn:
        conn.close()
    return {row["habit_id"]: row["count"] for row in rows}

def get_monthly_summary(owner):
    conn = get_connection()
    today = date.today()
    month_start = today.replace(day=1)
    days_in_month = (today - month_start).days + 1

    habits = conn.execute("SELECT * FROM habits WHERE owner = ? ORDER BY created_at DESC", (owner,)).fetchall()
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

def get_player_state(owner, conn=None):
    owns_conn = conn is None
    conn = conn or get_connection()
    row = conn.execute("SELECT xp, level FROM player_state WHERE owner = ?", (owner,)).fetchone()
    if owns_conn:
        conn.close()
    level = row["level"] if row else 1
    xp = row["xp"] if row else 0
    needed = xp_for_level(level)
    return {
        "xp": xp,
        "level": level,
        "xp_needed": needed,
        "pct": int(xp / needed * 100),
        "rank": rank_for_level(level),
    }

def add_xp(owner, amount):
    # BEGIN IMMEDIATE acquires the write lock up front, so a second concurrent
    # call blocks until the first commits instead of both reading the same
    # stale xp/level and one overwriting the other's contribution (a lost
    # update - confirmed to lose the vast majority of awarded XP under
    # concurrent /log requests without this).
    conn = get_connection()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT xp, level FROM player_state WHERE owner = ?", (owner,)).fetchone()
        xp = (row["xp"] if row else 0) + amount
        level = row["level"] if row else 1
        needed = xp_for_level(level)
        while xp >= needed:
            xp -= needed
            level += 1
            needed = xp_for_level(level)
        conn.execute(
            "INSERT INTO player_state (owner, xp, level) VALUES (?, ?, ?) "
            "ON CONFLICT(owner) DO UPDATE SET xp = excluded.xp, level = excluded.level",
            (owner, xp, level)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def get_streak(habit_id, repeat_days, today=None, conn=None):
    owns_conn = conn is None
    conn = conn or get_connection()
    rows = conn.execute(
        "SELECT logged_date FROM logs WHERE habit_id = ? ORDER BY logged_date DESC",
        (habit_id,)
    ).fetchall()
    if owns_conn:
        conn.close()

    if not rows:
        return 0

    dates = set(date.fromisoformat(row["logged_date"]) for row in rows)
    repeat_days = repeat_days or "1111111"
    today = today or date.today()
    streak = 0
    check = today
    # Bounded to a decade back: a habit scheduled on zero days (shouldn't
    # happen post-fix, but legacy rows could exist) would otherwise never
    # hit the "scheduled but not logged" break below and loop forever.
    for _ in range(3660):
        if repeat_days[check.weekday()] == "1":
            if check in dates:
                streak += 1
            elif check == today:
                pass  # today isn't over yet - a miss so far doesn't break the streak
            else:
                break
        check -= timedelta(days=1)

    return streak