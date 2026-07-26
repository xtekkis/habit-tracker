import os
import uuid
from datetime import timedelta

from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, session
from database import init_db, get_connection, get_streak, get_monthly_summary, get_weekly_counts, get_categories, get_todays_notes, get_habit, get_logs_for_month, count_perfect_days, get_preferences, set_preference, get_week_start, get_best_streak, get_recent_notes, add_xp, get_player_state

app = Flask(__name__)

# SECRET_KEY must be set (and kept stable) in the real deployment's environment -
# it signs each browser's session cookie. If it changes, every existing cookie
# stops validating and all anonymous per-browser data becomes unreachable.
IS_PRODUCTION = os.environ.get("SECRET_KEY") is not None
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-do-not-use-in-production")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)

@app.before_request
def assign_browser_identity():
    session.permanent = True
    if "uid" not in session:
        session["uid"] = uuid.uuid4().hex

PALETTE = ['#D96A34', '#8E9B4B', '#E8A93C', '#DD8FBE', '#C56B4A', '#7C9082', '#B58463', '#5A3A2A']

@app.context_processor
def inject_player():
    return {"player": get_player_state(session["uid"])}

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

@app.route("/robots.txt")
def robots():
    return send_from_directory("static", "robots.txt", mimetype="text/plain")

@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response

@app.route("/")
def index():
    from datetime import date, timedelta
    today_date = date.today()
    today = today_date.isoformat()
    owner = session["uid"]

    conn = get_connection()
    habits = conn.execute("""
        SELECT h.*, c.name as category_name
        FROM habits h
        LEFT JOIN categories c ON h.category_id = c.id AND c.owner = h.owner
        WHERE h.owner = ?
        ORDER BY h.created_at DESC
    """, (owner,)).fetchall()
    logged_today = set(
        row["habit_id"] for row in conn.execute("""
            SELECT l.habit_id FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE l.logged_date = ? AND h.owner = ?
        """, (today, owner)).fetchall()
    )
    conn.close()

    today_weekday = today_date.weekday()  # Mon=0..Sun=6
    habits = [h for h in habits if (h["repeat_days"] or "1111111")[today_weekday] == "1"]

    prefs = get_preferences(owner)
    week_start = get_week_start(today_date, prefs["start_week"])
    letters = ['M','T','W','T','F','S','S']  # indexed by Python weekday: Mon=0..Sun=6
    week_days = [
        {
            'label': letters[(week_start + timedelta(days=i)).weekday()],
            'number': (week_start + timedelta(days=i)).day,
            'is_today': (week_start + timedelta(days=i)) == today_date
        }
        for i in range(7)
    ]

    day_name = today_date.strftime("%A")
    day_month = f"{today_date.day} {today_date.strftime('%B')}"

    streaks = {habit["id"]: get_streak(habit["id"]) for habit in habits}
    weekly_counts = get_weekly_counts(owner, prefs["start_week"])
    categories = get_categories(owner)
    todays_notes = get_todays_notes(owner)
    pending_reminders = [
        {"name": h["name"], "time": h["reminder_time"]}
        for h in habits
        if h["reminder_time"] and h["id"] not in logged_today
    ]
    return render_template("index.html",
        habits=habits,
        streaks=streaks,
        logged_today=logged_today,
        weekly_counts=weekly_counts,
        categories=categories,
        todays_notes=todays_notes,
        week_days=week_days,
        day_name=day_name,
        day_month=day_month,
        pending_reminders=pending_reminders,
        show_streaks=prefs["show_streaks"] == "1",
    )

@app.route("/add", methods=["POST"])
def add_habit():
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    reminder_time = request.form.get("reminder_time") or None
    icon = request.form.get("icon") or "check"
    conn = get_connection()
    existing_count = conn.execute("SELECT COUNT(*) FROM habits WHERE owner = ?", (owner,)).fetchone()[0]
    default_color = PALETTE[existing_count % len(PALETTE)]
    color = request.form.get("color") or default_color
    if name:
        try:
            conn.execute(
                "INSERT INTO habits (name, category_id, repeat_days, reminder_time, icon, color, owner) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, category_id, repeat_days, reminder_time, icon, color, owner)
            )
            conn.commit()
        except Exception:
            pass
    conn.close()
    return redirect(url_for("index"))

@app.route("/delete/<int:habit_id>", methods=["POST"])
def delete_habit(habit_id):
    owner = session["uid"]
    conn = get_connection()
    owned = conn.execute("SELECT id FROM habits WHERE id = ? AND owner = ?", (habit_id, owner)).fetchone()
    if owned:
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        conn.execute("DELETE FROM logs WHERE habit_id = ?", (habit_id,))
        conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/monthly")
def monthly_summary():
    summary = get_monthly_summary(session["uid"])
    return render_template("monthly.html", summary=summary)

@app.route("/weekly")
def weekly_summary():
    from datetime import date
    owner = session["uid"]
    today = date.today()
    prefs = get_preferences(owner)
    week_start = get_week_start(today, prefs["start_week"])
    month_start = today.replace(day=1)
    days_elapsed_this_week = (today - week_start).days + 1

    conn = get_connection()
    habits = conn.execute("SELECT id, name, category_id, color FROM habits WHERE owner = ? ORDER BY created_at DESC", (owner,)).fetchall()

    habit_data = []
    for habit in habits:
        week_count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE habit_id = ? AND logged_date BETWEEN ? AND ?",
            (habit['id'], week_start.isoformat(), today.isoformat())
        ).fetchone()[0]
        month_count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE habit_id = ? AND logged_date BETWEEN ? AND ?",
            (habit['id'], month_start.isoformat(), today.isoformat())
        ).fetchone()[0]
        habit_data.append({
            'name': habit['name'],
            'color': habit['color'] or '#D96A34',
            'week_count': week_count,
            'week_pct': int(week_count / days_elapsed_this_week * 100),
            'month_count': month_count,
            'month_pct': int(month_count / today.day * 100),
        })
    conn.close()

    streaks = [get_streak(h['id']) for h in habits]
    best_streak = max(streaks) if streaks else 0

    week_total = sum(h['week_count'] for h in habit_data)
    week_active = sum(1 for h in habit_data if h['week_count'] > 0)
    week_possible = len(habits) * days_elapsed_this_week
    week_rate = int(week_total / week_possible * 100) if week_possible > 0 else 0

    month_total = sum(h['month_count'] for h in habit_data)
    month_active = sum(1 for h in habit_data if h['month_count'] > 0)
    month_possible = len(habits) * today.day
    month_rate = int(month_total / month_possible * 100) if month_possible > 0 else 0

    habit_count = len(habits)
    perfect_week = count_perfect_days(week_start.isoformat(), today.isoformat(), habit_count, owner)
    perfect_month = count_perfect_days(month_start.isoformat(), today.isoformat(), habit_count, owner)

    return render_template("weekly.html",
        habit_data=habit_data,
        best_streak=best_streak,
        week_total=week_total, week_active=week_active, week_rate=week_rate,
        month_total=month_total, month_active=month_active, month_rate=month_rate,
        perfect_week=perfect_week, perfect_month=perfect_month,
    )

@app.route("/edit/<int:habit_id>", methods=["POST"])
def edit_habit(habit_id):
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    reminder_time = request.form.get("reminder_time") or None
    icon = request.form.get("icon") or "check"
    color = request.form.get("color") or "#D96A34"
    if name:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE habits SET name = ?, category_id = ?, repeat_days = ?, reminder_time = ?, icon = ?, color = ? WHERE id = ? AND owner = ?",
                (name, category_id, repeat_days, reminder_time, icon, color, habit_id, owner)
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
    return redirect(url_for("index"))

@app.route("/category/add", methods=["POST"])
def add_category():
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:50]
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    if not name:
        return (jsonify({"ok": False}), 400) if is_fetch else redirect(url_for("index"))

    conn = get_connection()
    try:
        conn.execute("INSERT INTO categories (name, owner) VALUES (?, ?)", (name, owner))
        conn.commit()
    except Exception:
        conn.close()
        return (jsonify({"ok": False}), 400) if is_fetch else redirect(url_for("index"))

    if is_fetch:
        cat = conn.execute(
            "SELECT id, name FROM categories WHERE owner = ? AND name = ?", (owner, name)
        ).fetchone()
        conn.close()
        return jsonify({"ok": True, "id": cat["id"], "name": cat["name"]})

    conn.close()
    return redirect(url_for("index"))

@app.route("/category/delete/<int:category_id>", methods=["POST"])
def delete_category(category_id):
    owner = session["uid"]
    conn = get_connection()
    conn.execute("UPDATE habits SET category_id = NULL WHERE category_id = ? AND owner = ?", (category_id, owner))
    conn.execute("DELETE FROM categories WHERE id = ? AND owner = ?", (category_id, owner))
    conn.commit()
    conn.close()
    next_url = request.form.get('next', url_for('index'))
    if not next_url.startswith('/'):
        next_url = url_for('index')
    return redirect(next_url)

@app.route("/calendar")
def calendar_index():
    owner = session["uid"]
    conn = get_connection()
    habits = conn.execute("""
        SELECT h.*, c.name as category_name
        FROM habits h
        LEFT JOIN categories c ON h.category_id = c.id AND c.owner = h.owner
        WHERE h.owner = ?
        ORDER BY h.created_at DESC
    """, (owner,)).fetchall()
    conn.close()
    return render_template("calendar_index.html", habits=habits)

@app.route("/settings")
def settings():
    owner = session["uid"]
    categories = get_categories(owner)
    prefs = get_preferences(owner)
    return render_template("settings.html", categories=categories, prefs=prefs)

@app.route("/preferences/toggle-streaks", methods=["POST"])
def toggle_streaks():
    owner = session["uid"]
    prefs = get_preferences(owner)
    new_value = "0" if prefs["show_streaks"] == "1" else "1"
    set_preference(owner, "show_streaks", new_value)
    return redirect(url_for("settings"))

@app.route("/preferences/start-week", methods=["POST"])
def set_start_week():
    value = request.form.get("value")
    if value in ("monday", "sunday"):
        set_preference(session["uid"], "start_week", value)
    return redirect(url_for("settings"))

@app.route("/reset", methods=["POST"])
def reset_all():
    owner = session["uid"]
    conn = get_connection()
    conn.execute("DELETE FROM logs WHERE habit_id IN (SELECT id FROM habits WHERE owner = ?)", (owner,))
    conn.execute("DELETE FROM habits WHERE owner = ?", (owner,))
    conn.execute("DELETE FROM categories WHERE owner = ?", (owner,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/habit/<int:habit_id>/calendar")
def habit_calendar(habit_id):
    import calendar as cal_module
    from datetime import date

    habit = get_habit(habit_id, session["uid"])
    if not habit:
        return redirect(url_for("index"))

    today = date.today()
    month_param = request.args.get("month", f"{today.year:04d}-{today.month:02d}")
    try:
        year, month = int(month_param[:4]), int(month_param[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    logs = get_logs_for_month(habit_id, year, month)
    weeks = cal_module.monthcalendar(year, month)
    month_name = date(year, month, 1).strftime("%B %Y")

    if month == 1:
        prev_month = f"{year-1:04d}-12"
    else:
        prev_month = f"{year:04d}-{month-1:02d}"

    if month == 12:
        next_month = f"{year+1:04d}-01"
    else:
        next_month = f"{year:04d}-{month+1:02d}"

    is_current = (year == today.year and month == today.month)
    today_day = today.day if is_current else None

    logged_days = set(int(d.split('-')[2]) for d in logs.keys())
    month_logged = len(logs)
    month_days = today.day if is_current else cal_module.monthrange(year, month)[1]
    streak = get_streak(habit_id)
    best_streak = get_best_streak(habit_id)
    raw_notes = get_recent_notes(habit_id)
    recent_notes = [
        {"date": f"{date.fromisoformat(n['date']).day} {date.fromisoformat(n['date']).strftime('%b')}", "notes": n["notes"]}
        for n in raw_notes
    ]

    return render_template("calendar.html",
        habit=habit,
        weeks=weeks,
        logged_days=logged_days,
        month_name=month_name,
        year=year,
        month=month,
        prev_month=prev_month,
        next_month=next_month,
        today_day=today_day,
        streak=streak,
        best_streak=best_streak,
        recent_notes=recent_notes,
        month_logged=month_logged,
        month_days=month_days,
    )

@app.route("/export/csv")
def export_csv():
    import csv
    import io
    from flask import Response

    conn = get_connection()
    rows = conn.execute("""
        SELECT h.name AS habit, l.logged_date AS date, l.notes
        FROM logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE h.owner = ?
        ORDER BY h.name, l.logged_date
    """, (session["uid"],)).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Habit", "Date", "Notes"])
    for row in rows:
        writer.writerow([row["habit"], row["date"], row["notes"] or ""])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=habits.csv"}
    )

@app.route("/log/<int:habit_id>", methods=["POST"])
def log_habit(habit_id):
    from datetime import date
    owner = session["uid"]
    conn = get_connection()

    owned = conn.execute("SELECT id FROM habits WHERE id = ? AND owner = ?", (habit_id, owner)).fetchone()
    if not owned:
        conn.close()
        return jsonify({"done": False}), 404

    notes = request.form.get("notes", "").strip()[:500]
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO logs (habit_id, notes) VALUES (?, ?)",
        (habit_id, notes or None)
    )
    conn.commit()
    new_log = cursor.rowcount > 0

    today = date.today()
    today_weekday = today.weekday()
    all_habits = conn.execute("SELECT id, repeat_days FROM habits WHERE owner = ?", (owner,)).fetchall()
    scheduled = [h for h in all_habits if (h["repeat_days"] or "1111111")[today_weekday] == "1"]
    logged_count = conn.execute("""
        SELECT COUNT(*) FROM logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE l.logged_date = ? AND h.owner = ?
    """, (today.isoformat(), owner)).fetchone()[0]
    conn.close()

    if new_log:
        add_xp(owner, 10)
        if scheduled and logged_count >= len(scheduled):
            add_xp(owner, 25)

    return jsonify({
        "done": new_log,
        "logged_count": logged_count,
        "player": get_player_state(owner)
    })

if __name__ == "__main__":
    init_db()
    app.run(debug=not IS_PRODUCTION)
