from flask import Flask, render_template, request, redirect, url_for
from database import init_db, get_connection, get_streak, get_weekly_summary, get_monthly_summary, get_weekly_counts

app = Flask(__name__)

@app.route("/")
def index():
    from datetime import date
    today = date.today().isoformat()
    conn = get_connection()
    habits = conn.execute("SELECT * FROM habits ORDER BY created_at DESC").fetchall()
    logged_today = set(
        row["habit_id"] for row in conn.execute(
            "SELECT habit_id FROM logs WHERE logged_date = ?", (today,)
        ).fetchall()
    )
    conn.close()
    streaks = {habit["id"]: get_streak(habit["id"]) for habit in habits}
    weekly_counts = get_weekly_counts()
    return render_template("index.html", habits=habits, streaks=streaks, logged_today=logged_today, weekly_counts=weekly_counts)

@app.route("/add", methods=["POST"])
def add_habit():
    name = request.form.get("name", "").strip()
    if name:
        conn = get_connection()
        try:
            conn.execute("INSERT INTO habits (name) VALUES (?)", (name,))
            conn.commit()
        except:
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
    name = request.form.get("name", "").strip()
    if name:
        conn = get_connection()
        try:
            conn.execute("UPDATE habits SET name = ? WHERE id = ?", (name, habit_id))
            conn.commit()
        except:
            pass
        conn.close()
    return redirect(url_for("index"))

@app.route("/log/<int:habit_id>", methods=["POST"])
def log_habit(habit_id):
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO logs (habit_id) VALUES (?)", (habit_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)