from flask import Flask, render_template, request, redirect, url_for
from database import init_db, get_connection, get_streak, get_weekly_summary, get_monthly_summary, get_weekly_counts, get_categories, get_todays_notes, get_habit, get_logs_for_month

app = Flask(__name__)

@app.route("/")
def index():
    from datetime import date
    today = date.today().isoformat()
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
    streaks = {habit["id"]: get_streak(habit["id"]) for habit in habits}
    weekly_counts = get_weekly_counts()
    categories = get_categories()
    todays_notes = get_todays_notes()
    return render_template("index.html", habits=habits, streaks=streaks, logged_today=logged_today, weekly_counts=weekly_counts, categories=categories, todays_notes=todays_notes)

@app.route("/add", methods=["POST"])
def add_habit():
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    if name:
        conn = get_connection()
        try:
            conn.execute("INSERT INTO habits (name, category_id) VALUES (?, ?)", (name, category_id))
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
    summary = get_weekly_summary()
    return render_template("weekly.html", summary=summary)

@app.route("/edit/<int:habit_id>", methods=["POST"])
def edit_habit(habit_id):
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    if name:
        conn = get_connection()
        try:
            conn.execute("UPDATE habits SET name = ?, category_id = ? WHERE id = ?", (name, category_id, habit_id))
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

@app.route("/habit/<int:habit_id>/calendar")
def habit_calendar(habit_id):
    import calendar
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
    weeks = calendar.monthcalendar(year, month)
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

    return render_template("calendar.html",
        habit=habit,
        weeks=weeks,
        logs=logs,
        month_name=month_name,
        year=year,
        month=month,
        prev_month=prev_month,
        next_month=next_month,
        today_day=today_day
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
