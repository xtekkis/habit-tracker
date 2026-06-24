from flask import Flask, render_template, request, redirect, url_for
from database import init_db, get_connection, get_streak, get_weekly_summary, get_monthly_summary, get_weekly_counts, get_categories, get_todays_notes

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
