from flask import Flask, render_template, request, redirect, url_for
from database import init_db, get_connection, get_streak

app = Flask(__name__)

@app.route("/")
def index():
    conn = get_connection()
    habits = conn.execute("SELECT * FROM habits ORDER BY created_at DESC").fetchall()
    conn.close()
    streaks = {habit["id"]: get_streak(habit["id"]) for habit in habits}
    return render_template("index.html", habits=habits, streaks=streaks)

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