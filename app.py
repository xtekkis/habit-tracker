from flask import Flask, render_template, request, redirect, url_for
from database import init_db, get_connection, get_streak, get_monthly_summary, get_weekly_counts, get_categories, get_todays_notes, get_habit, get_logs_for_month

app = Flask(__name__)

@app.route("/")
def index():
    from datetime import date, timedelta
    today_date = date.today()
    today = today_date.isoformat()

    conn = get_connection()
    habits = conn.execute("""
        SELECT h.*, c.name as category_name
        FROM habits h
        LEFT JOIN categories c ON h.category_id = c.id
        ORDER BY h.created_at DESC
    """).fetchall()
    logged_today = set(
        row["habit_id"] for row in conn.execute(
            "SELECT habit_id FROM logs WHERE logged_date = ?", (today,)
        ).fetchall()
    )
    conn.close()

    monday = today_date - timedelta(days=today_date.weekday())
    week_days = [
        {
            'label': ['M','T','W','T','F','S','S'][i],
            'number': (monday + timedelta(days=i)).day,
            'is_today': (monday + timedelta(days=i)) == today_date
        }
        for i in range(7)
    ]

    day_name = today_date.strftime("%A")
    day_month = f"{today_date.day} {today_date.strftime('%B')}"

    streaks = {habit["id"]: get_streak(habit["id"]) for habit in habits}
    weekly_counts = get_weekly_counts()
    categories = get_categories()
    todays_notes = get_todays_notes()
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
    )

@app.route("/add", methods=["POST"])
def add_habit():
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    reminder_time = request.form.get("reminder_time") or None
    if name:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO habits (name, category_id, repeat_days, reminder_time) VALUES (?, ?, ?, ?)",
                (name, category_id, repeat_days, reminder_time)
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
    return redirect(url_for("index"))

@app.route("/delete/<int:habit_id>")
def delete_habit(habit_id):
    conn = get_connection()
    conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    conn.execute("DELETE FROM logs WHERE habit_id = ?", (habit_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/monthly")
def monthly_summary():
    summary = get_monthly_summary()
    return render_template("monthly.html", summary=summary)

@app.route("/weekly")
def weekly_summary():
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday of current week
    month_start = today.replace(day=1)
    days_elapsed_this_week = today.weekday() + 1  # 1 on Monday, 7 on Sunday

    conn = get_connection()
    habits = conn.execute("SELECT id, name, category_id FROM habits ORDER BY created_at DESC").fetchall()

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
        cat_id = habit['category_id']
        tile_class = f'tile-{(cat_id - 1) % 8}' if cat_id else 'tile-none'
        habit_data.append({
            'name': habit['name'],
            'tile_class': tile_class,
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

    return render_template("weekly.html",
        habit_data=habit_data,
        best_streak=best_streak,
        week_total=week_total, week_active=week_active, week_rate=week_rate,
        month_total=month_total, month_active=month_active, month_rate=month_rate,
    )

@app.route("/edit/<int:habit_id>", methods=["POST"])
def edit_habit(habit_id):
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    reminder_time = request.form.get("reminder_time") or None
    if name:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE habits SET name = ?, category_id = ?, repeat_days = ?, reminder_time = ? WHERE id = ?",
                (name, category_id, repeat_days, reminder_time, habit_id)
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
    return redirect(url_for("index"))

@app.route("/category/add", methods=["POST"])
def add_category():
    name = request.form.get("name", "").strip()[:50]
    if name:
        conn = get_connection()
        try:
            conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
            conn.commit()
        except Exception:
            pass
        conn.close()
    return redirect(url_for("index"))

@app.route("/category/delete/<int:category_id>")
def delete_category(category_id):
    conn = get_connection()
    conn.execute("UPDATE habits SET category_id = NULL WHERE category_id = ?", (category_id,))
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    next_url = request.args.get('next', url_for('index'))
    if not next_url.startswith('/'):
        next_url = url_for('index')
    return redirect(next_url)

@app.route("/calendar")
def calendar_index():
    conn = get_connection()
    habits = conn.execute("""
        SELECT h.*, c.name as category_name
        FROM habits h
        LEFT JOIN categories c ON h.category_id = c.id
        ORDER BY h.created_at DESC
    """).fetchall()
    conn.close()
    return render_template("calendar_index.html", habits=habits)

@app.route("/settings")
def settings():
    categories = get_categories()
    return render_template("settings.html", categories=categories)

@app.route("/reset", methods=["POST"])
def reset_all():
    conn = get_connection()
    conn.execute("DELETE FROM logs")
    conn.execute("DELETE FROM habits")
    conn.execute("DELETE FROM categories")
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/habit/<int:habit_id>/calendar")
def habit_calendar(habit_id):
    import calendar as cal_module
    from datetime import date

    habit = get_habit(habit_id)
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
        ORDER BY h.name, l.logged_date
    """).fetchall()
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
    notes = request.form.get("notes", "").strip()[:500]
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO logs (habit_id, notes) VALUES (?, ?)",
        (habit_id, notes or None)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
