import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, session, flash, g
from flask.sessions import SecureCookieSessionInterface
from database import init_db, db_connection, get_connection, get_streak, get_weekly_counts, get_categories, get_habit, get_logged_dates_for_month, count_perfect_days, get_preferences, set_preference, get_week_start, get_best_streak, add_xp, get_player_state, category_belongs_to_owner, reorder_habits, get_archived_habits

app = Flask(__name__)

STATIC_LIKE_PATHS = ("/robots.txt", "/sw.js", "/manifest.json")

class _NoCookieOnStaticSessionInterface(SecureCookieSessionInterface):
    # Static assets never need identity, so they shouldn't force a Set-Cookie
    # on every request. Flask's session-saving step runs after all
    # after_request hooks, so suppressing it has to happen here, at the one
    # point Flask actually checks before deciding to send the cookie.
    def should_set_cookie(self, app, session):
        if request.path.startswith("/static/") or request.path in STATIC_LIKE_PATHS:
            return False
        return super().should_set_cookie(app, session)

app.session_interface = _NoCookieOnStaticSessionInterface()

# SECRET_KEY must be set (and kept stable) in the real deployment's environment -
# it signs each browser's session cookie. If it changes, every existing cookie
# stops validating and all anonymous per-browser data becomes unreachable.
_secret_key_env = os.environ.get("SECRET_KEY")
IS_PRODUCTION = _secret_key_env is not None
app.secret_key = _secret_key_env or "dev-only-secret-do-not-use-in-production"

if not IS_PRODUCTION:
    print(
        "\n"
        "!!! WARNING: SECRET_KEY is not set. Using a public, insecure development\n"
        "!!! secret. Session cookies can be forged and per-browser data is NOT\n"
        "!!! private. Set the SECRET_KEY environment variable before deploying\n"
        "!!! this app anywhere it will be reachable by others.\n",
        file=sys.stderr
    )

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)

@app.before_request
def assign_browser_identity():
    # Only assign a uid when one doesn't exist yet; setting session.permanent
    # unconditionally would mark the session "modified" on every request for
    # no reason. Static-asset paths are additionally exempted from ever
    # getting a Set-Cookie at all, via the session interface above.
    if "uid" not in session:
        session["uid"] = uuid.uuid4().hex
        session.permanent = True

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self'; "
        "img-src 'self'; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response

# In-memory rate limiting, in two tiers, so a scripted loop can't spam writes
# without also blocking real people.
#
# Tier 1 is per browser session and per endpoint, with limits set well above
# normal use. This is the one real users could ever bump into.
#
# Tier 2 is a much higher shared budget across every write endpoint, keyed by
# IP. A request that sends no cookie gets a fresh uid every time, so tier 1
# alone is trivially bypassed by clearing cookies; the IP budget is the
# backstop for that. Keying tier 1 by IP instead was the original approach and
# it was wrong: everyone behind one router or mobile carrier NAT shared a
# single allowance, so one person adding 10 habits locked out everybody else
# on that network, and one person adding 11 habits in a sitting locked out
# themselves.
#
# Both reset on app restart; fine for a single-process, low-traffic deploy.
_rate_limit_hits = defaultdict(list)
_rate_limit_calls_since_sweep = 0
_RATE_LIMIT_SWEEP_EVERY = 100
_RATE_LIMIT_SWEEP_MAX_AGE = 3600  # comfortably above any window_seconds in use below
_IP_BUDGET = 300                  # writes per IP per window, across all endpoints
_IP_BUDGET_WINDOW = 600

def _sweep_rate_limit_hits(now):
    # A key is never removed just because that session or IP stops showing
    # up, so without this the dict grows by one entry per distinct
    # session/endpoint and IP ever seen, for the life of the process. Runs
    # periodically rather than every call, since it walks every key.
    cutoff = now - _RATE_LIMIT_SWEEP_MAX_AGE
    stale_keys = [key for key, hits in _rate_limit_hits.items() if not any(t >= cutoff for t in hits)]
    for key in stale_keys:
        del _rate_limit_hits[key]

def _over_limit(key, limit, window_seconds, now):
    cutoff = now - window_seconds
    hits = [t for t in _rate_limit_hits[key] if t >= cutoff]
    _rate_limit_hits[key] = hits
    return len(hits) >= limit

def rate_limit(limit=30, window_seconds=600):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            global _rate_limit_calls_since_sweep
            now = time.time()

            _rate_limit_calls_since_sweep += 1
            if _rate_limit_calls_since_sweep >= _RATE_LIMIT_SWEEP_EVERY:
                _rate_limit_calls_since_sweep = 0
                _sweep_rate_limit_hits(now)

            session_key = ("s", session.get("uid"), f.__name__)
            ip_key = ("ip", request.remote_addr)

            if (_over_limit(session_key, limit, window_seconds, now)
                    or _over_limit(ip_key, _IP_BUDGET, _IP_BUDGET_WINDOW, now)):
                error = "Too many requests. Try again in a few minutes."
                # request.is_json covers the reorder endpoint, which posts a
                # JSON body without the X-Requested-With header the other
                # fetch callers send.
                if request.headers.get("X-Requested-With") == "fetch" or request.is_json:
                    return jsonify({"ok": False, "done": False, "error": error}), 429
                flash(error, "error")
                return redirect(url_for("index"))

            _rate_limit_hits[session_key].append(now)
            _rate_limit_hits[ip_key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

def get_request_conn():
    # Reuse one SQLite connection for the whole request instead of opening a
    # fresh one for every helper call - a single Today render with 8 habits
    # was opening 13 separate connections (one per streak, plus
    # prefs/categories/player-state). Closed in _close_request_conn below,
    # which Flask guarantees runs even if the view raises.
    if "db_conn" not in g:
        g.db_conn = get_connection()
    return g.db_conn

@app.teardown_appcontext
def _close_request_conn(exception=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()

def get_local_today():
    # The server (PythonAnywhere) runs in UTC, which isn't the visitor's
    # timezone - base.html sets a `local_date` cookie from the browser's
    # clock on every page load, and the check-off fetch sends a fresh one
    # as a query param so it can't go stale if the tab's been open a while.
    # Real-world timezones only span UTC-12..UTC+14, so a genuine local date
    # can never be more than 1 day off the server's UTC date - anything
    # further out is untrusted client input, not a real timezone, so it's
    # rejected in favor of the server's own date rather than trusted blindly.
    from datetime import date
    server_today = date.today()
    raw = request.args.get("local_date") or request.cookies.get("local_date")
    if raw:
        try:
            candidate = date.fromisoformat(raw)
        except ValueError:
            return server_today
        if abs((candidate - server_today).days) <= 1:
            return candidate
    return server_today

PALETTE = ['#D96A34', '#8E9B4B', '#E8A93C', '#DD8FBE', '#C56B4A', '#7C9082', '#B58463', '#5A3A2A']

@app.context_processor
def inject_player():
    return {"player": get_player_state(session["uid"], get_request_conn())}

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
    from datetime import timedelta
    today_date = get_local_today()
    today = today_date.isoformat()
    owner = session["uid"]

    conn = get_request_conn()
    habits = conn.execute("""
        SELECT h.*, c.name as category_name
        FROM habits h
        LEFT JOIN categories c ON h.category_id = c.id AND c.owner = h.owner
        WHERE h.owner = ? AND h.archived_at IS NULL
        ORDER BY h.sort_order ASC, h.created_at DESC
    """, (owner,)).fetchall()
    logged_today = set(
        row["habit_id"] for row in conn.execute("""
            SELECT l.habit_id FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE l.logged_date = ? AND h.owner = ?
        """, (today, owner)).fetchall()
    )

    today_weekday = today_date.weekday()  # Mon=0..Sun=6
    habits = [h for h in habits if (h["repeat_days"] or "1111111")[today_weekday] == "1"]

    prefs = get_preferences(owner, conn)
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

    streaks = {habit["id"]: get_streak(habit["id"], habit["repeat_days"], today_date, conn) for habit in habits}
    weekly_counts = get_weekly_counts(owner, prefs["start_week"], today_date, conn)
    categories = get_categories(owner, conn)
    pending_reminders = [
        {"id": h["id"], "name": h["name"], "time": h["reminder_time"]}
        for h in habits
        if h["reminder_time"] and h["id"] not in logged_today
    ]
    return render_template("index.html",
        habits=habits,
        streaks=streaks,
        logged_today=logged_today,
        weekly_counts=weekly_counts,
        categories=categories,
        week_days=week_days,
        day_name=day_name,
        day_month=day_month,
        pending_reminders=pending_reminders,
        show_streaks=prefs["show_streaks"] == "1",
    )

@app.route("/add", methods=["POST"])
@rate_limit()
def add_habit():
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    if category_id and not category_belongs_to_owner(category_id, owner):
        category_id = None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    if repeat_days == '0000000':
        # Client-side validation should already prevent this, but a habit
        # scheduled on zero days would never appear on Today and could never
        # be checked off - default to every day rather than accept that.
        repeat_days = '1111111'
    reminder_time = request.form.get("reminder_time") or None
    icon = request.form.get("icon") or "check"
    with db_connection() as conn:
        existing_count = conn.execute("SELECT COUNT(*) FROM habits WHERE owner = ?", (owner,)).fetchone()[0]
        default_color = PALETTE[existing_count % len(PALETTE)]
        color = request.form.get("color") or default_color
        if name:
            try:
                conn.execute(
                    "INSERT INTO habits (name, category_id, repeat_days, reminder_time, icon, color, owner, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM habits WHERE owner = ?))",
                    (name, category_id, repeat_days, reminder_time, icon, color, owner, owner)
                )
                conn.commit()
            except sqlite3.IntegrityError:
                flash(f'A habit named "{name}" already exists.', 'error')
    return redirect(url_for("index"))

@app.route("/delete/<int:habit_id>", methods=["POST"])
@rate_limit()
def delete_habit(habit_id):
    owner = session["uid"]
    with db_connection() as conn:
        owned = conn.execute("SELECT id FROM habits WHERE id = ? AND owner = ?", (habit_id, owner)).fetchone()
        if owned:
            conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
            conn.execute("DELETE FROM logs WHERE habit_id = ?", (habit_id,))
            conn.commit()
    return redirect(url_for("index"))

@app.route("/habit/<int:habit_id>/archive", methods=["POST"])
@rate_limit()
def archive_habit(habit_id):
    from datetime import datetime
    owner = session["uid"]
    with db_connection() as conn:
        owned = conn.execute("SELECT id FROM habits WHERE id = ? AND owner = ? AND archived_at IS NULL", (habit_id, owner)).fetchone()
        if owned:
            conn.execute("UPDATE habits SET archived_at = ? WHERE id = ?", (datetime.now().isoformat(), habit_id))
            conn.commit()
    return redirect(url_for("index"))

@app.route("/habit/<int:habit_id>/unarchive", methods=["POST"])
@rate_limit()
def unarchive_habit(habit_id):
    owner = session["uid"]
    with db_connection() as conn:
        habit = conn.execute("SELECT id, name FROM habits WHERE id = ? AND owner = ? AND archived_at IS NOT NULL", (habit_id, owner)).fetchone()
        if habit:
            try:
                conn.execute("UPDATE habits SET archived_at = NULL WHERE id = ?", (habit_id,))
                conn.commit()
            except sqlite3.IntegrityError:
                # An active habit already has this name - restoring would
                # collide with the partial UNIQUE(owner, name) index.
                flash(f'Can\'t restore "{habit["name"]}" - you already have an active habit with that name.', 'error')
    return redirect(url_for("settings"))

@app.route("/habits/reorder", methods=["POST"])
@rate_limit(limit=60)  # one call per drag-and-drop drop, so reordering a list runs through these fast
def reorder():
    owner = session["uid"]
    body = request.get_json(silent=True) or {}
    subset_ids = body.get("order")
    if not isinstance(subset_ids, list) or not subset_ids:
        return jsonify({"ok": False, "error": "Invalid order."}), 400
    try:
        subset_ids = [int(habit_id) for habit_id in subset_ids]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid order."}), 400

    with db_connection() as conn:
        if not reorder_habits(owner, subset_ids, conn):
            return jsonify({"ok": False, "error": "That order is out of date - reload and try again."}), 409
    return jsonify({"ok": True})

@app.route("/weekly")
def weekly_summary():
    from datetime import date
    owner = session["uid"]
    today = get_local_today()
    conn = get_request_conn()
    prefs = get_preferences(owner, conn)
    week_start = get_week_start(today, prefs["start_week"])
    month_start = today.replace(day=1)

    habits = conn.execute("SELECT id, name, category_id, color, repeat_days, created_at FROM habits WHERE owner = ? AND archived_at IS NULL ORDER BY sort_order ASC, created_at DESC", (owner,)).fetchall()

    habit_data = []
    week_possible = 0
    month_possible = 0
    for habit in habits:
        # A habit's % is out of the days it's actually existed within the
        # period, not the whole period - otherwise a habit created today
        # (or this month) shows an unfairly low % for the rest of it, despite
        # a perfect record so far.
        created = date.fromisoformat(habit['created_at'])
        week_days_active = (today - max(week_start, created)).days + 1
        month_days_active = (today - max(month_start, created)).days + 1
        week_possible += week_days_active
        month_possible += month_days_active

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
            'week_pct': int(week_count / week_days_active * 100),
            'month_count': month_count,
            'month_pct': int(month_count / month_days_active * 100),
        })

    streaks = [get_streak(h['id'], h['repeat_days'], today, conn) for h in habits]
    best_streak = max(streaks) if streaks else 0

    week_total = sum(h['week_count'] for h in habit_data)
    week_active = sum(1 for h in habit_data if h['week_count'] > 0)
    week_rate = int(week_total / week_possible * 100) if week_possible > 0 else 0

    month_total = sum(h['month_count'] for h in habit_data)
    month_active = sum(1 for h in habit_data if h['month_count'] > 0)
    month_rate = int(month_total / month_possible * 100) if month_possible > 0 else 0

    perfect_week = count_perfect_days(week_start.isoformat(), today.isoformat(), owner, conn)
    perfect_month = count_perfect_days(month_start.isoformat(), today.isoformat(), owner, conn)

    return render_template("weekly.html",
        habit_data=habit_data,
        best_streak=best_streak,
        week_total=week_total, week_active=week_active, week_rate=week_rate,
        month_total=month_total, month_active=month_active, month_rate=month_rate,
        perfect_week=perfect_week, perfect_month=perfect_month,
    )

@app.route("/edit/<int:habit_id>", methods=["POST"])
@rate_limit()
def edit_habit(habit_id):
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:100]
    category_id = request.form.get("category_id") or None
    if category_id and not category_belongs_to_owner(category_id, owner):
        category_id = None
    repeat_days = ''.join('1' if request.form.get(f'day_{i}') else '0' for i in range(7))
    if repeat_days == '0000000':
        repeat_days = '1111111'
    reminder_time = request.form.get("reminder_time") or None
    icon = request.form.get("icon") or "check"
    color = request.form.get("color") or "#D96A34"
    if name:
        with db_connection() as conn:
            try:
                conn.execute(
                    "UPDATE habits SET name = ?, category_id = ?, repeat_days = ?, reminder_time = ?, icon = ?, color = ? WHERE id = ? AND owner = ?",
                    (name, category_id, repeat_days, reminder_time, icon, color, habit_id, owner)
                )
                conn.commit()
            except sqlite3.IntegrityError:
                flash(f'A habit named "{name}" already exists.', 'error')
    return redirect(url_for("index"))

@app.route("/category/add", methods=["POST"])
@rate_limit()
def add_category():
    owner = session["uid"]
    name = request.form.get("name", "").strip()[:50]
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    if not name:
        error = "Category name is required."
        if is_fetch:
            return jsonify({"ok": False, "error": error}), 400
        flash(error, "error")
        return redirect(url_for("index"))

    with db_connection() as conn:
        try:
            conn.execute("INSERT INTO categories (name, owner) VALUES (?, ?)", (name, owner))
            conn.commit()
        except sqlite3.IntegrityError:
            error = f'A category named "{name}" already exists.'
            if is_fetch:
                return jsonify({"ok": False, "error": error}), 400
            flash(error, "error")
            return redirect(url_for("index"))

        if is_fetch:
            cat = conn.execute(
                "SELECT id, name FROM categories WHERE owner = ? AND name = ?", (owner, name)
            ).fetchone()
            return jsonify({"ok": True, "id": cat["id"], "name": cat["name"]})

    return redirect(url_for("index"))

@app.route("/category/delete/<int:category_id>", methods=["POST"])
@rate_limit()
def delete_category(category_id):
    owner = session["uid"]
    with db_connection() as conn:
        conn.execute("UPDATE habits SET category_id = NULL WHERE category_id = ? AND owner = ?", (category_id, owner))
        conn.execute("DELETE FROM categories WHERE id = ? AND owner = ?", (category_id, owner))
        conn.commit()
    next_url = request.form.get('next')
    if next_url not in ('/', '/settings'):
        next_url = url_for('index')
    return redirect(next_url)

@app.route("/calendar")
def calendar_index():
    owner = session["uid"]
    with db_connection() as conn:
        habits = conn.execute("""
            SELECT h.*, c.name as category_name
            FROM habits h
            LEFT JOIN categories c ON h.category_id = c.id AND c.owner = h.owner
            WHERE h.owner = ? AND h.archived_at IS NULL
            ORDER BY h.sort_order ASC, h.created_at DESC
        """, (owner,)).fetchall()
    return render_template("calendar_index.html", habits=habits)

@app.route("/settings")
def settings():
    owner = session["uid"]
    conn = get_request_conn()
    categories = get_categories(owner, conn)
    prefs = get_preferences(owner, conn)
    archived_habits = get_archived_habits(owner, conn)
    return render_template("settings.html", categories=categories, prefs=prefs, archived_habits=archived_habits)

@app.route("/preferences/toggle-streaks", methods=["POST"])
@rate_limit()
def toggle_streaks():
    owner = session["uid"]
    prefs = get_preferences(owner)
    new_value = "0" if prefs["show_streaks"] == "1" else "1"
    set_preference(owner, "show_streaks", new_value)
    return redirect(url_for("settings"))

@app.route("/preferences/start-week", methods=["POST"])
@rate_limit()
def set_start_week():
    value = request.form.get("value")
    if value in ("monday", "sunday"):
        set_preference(session["uid"], "start_week", value)
    return redirect(url_for("settings"))

@app.route("/reset", methods=["POST"])
@rate_limit(limit=5)  # destructive and wipes everything, so far tighter than the rest
def reset_all():
    owner = session["uid"]
    with db_connection() as conn:
        conn.execute("DELETE FROM logs WHERE habit_id IN (SELECT id FROM habits WHERE owner = ?)", (owner,))
        conn.execute("DELETE FROM habits WHERE owner = ?", (owner,))
        conn.execute("DELETE FROM categories WHERE owner = ?", (owner,))
        conn.commit()
    return redirect(url_for("index"))

@app.route("/habit/<int:habit_id>/calendar")
def habit_calendar(habit_id):
    import calendar as cal_module
    from datetime import date

    habit = get_habit(habit_id, session["uid"])
    if not habit:
        return redirect(url_for("index"))

    today = get_local_today()
    month_param = request.args.get("month", f"{today.year:04d}-{today.month:02d}")
    try:
        year, month = int(month_param[:4]), int(month_param[5:7])
        if not (1 <= month <= 12) or not (1 <= year <= 9999):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    logged_dates = get_logged_dates_for_month(habit_id, year, month)
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

    logged_days = set(int(d.split('-')[2]) for d in logged_dates)
    month_logged = len(logged_dates)
    month_days = today.day if is_current else cal_module.monthrange(year, month)[1]
    streak = get_streak(habit_id, habit["repeat_days"], today)
    best_streak = get_best_streak(habit_id, habit["repeat_days"])

    # Backfilling is deliberately limited to exactly one day back, so it's
    # only ever offered when yesterday falls within the month being viewed,
    # was actually a scheduled day for this habit, and isn't logged yet.
    yesterday = today - timedelta(days=1)
    yesterday_day = yesterday.day if (yesterday.year == year and yesterday.month == month) else None
    yesterday_scheduled = (habit["repeat_days"] or "1111111")[yesterday.weekday()] == "1"
    can_backfill_yesterday = (
        bool(yesterday_day) and yesterday_scheduled
        and yesterday_day not in logged_days and habit["archived_at"] is None
    )

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
        month_logged=month_logged,
        month_days=month_days,
        yesterday_day=yesterday_day,
        can_backfill_yesterday=can_backfill_yesterday,
    )

@app.route("/habit/<int:habit_id>/log-yesterday", methods=["POST"])
@rate_limit()
def log_yesterday(habit_id):
    # Deliberately scoped to exactly one day back, computed server-side from
    # the request's own local-today resolution - never client-supplied - so
    # this can't be widened into an open-ended backdate-anything endpoint.
    owner = session["uid"]
    yesterday = get_local_today() - timedelta(days=1)

    with db_connection() as conn:
        habit = conn.execute(
            "SELECT id, repeat_days FROM habits WHERE id = ? AND owner = ? AND archived_at IS NULL",
            (habit_id, owner)
        ).fetchone()
        if not habit:
            return jsonify({"done": False}), 404
        if (habit["repeat_days"] or "1111111")[yesterday.weekday()] != "1":
            return jsonify({"done": False, "error": "This habit wasn't scheduled yesterday."}), 400

        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO logs (habit_id, logged_date) VALUES (?, ?)",
            (habit_id, yesterday.isoformat())
        )
        conn.commit()
        new_log = cursor.rowcount > 0

        yesterday_weekday = yesterday.weekday()
        all_habits = conn.execute("SELECT id, repeat_days FROM habits WHERE owner = ? AND archived_at IS NULL", (owner,)).fetchall()
        scheduled_ids = {h["id"] for h in all_habits if (h["repeat_days"] or "1111111")[yesterday_weekday] == "1"}
        logged_ids = {row["habit_id"] for row in conn.execute("""
            SELECT l.habit_id FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE l.logged_date = ? AND h.owner = ?
        """, (yesterday.isoformat(), owner)).fetchall()}

    if new_log:
        add_xp(owner, 10)
        if scheduled_ids and scheduled_ids.issubset(logged_ids):
            add_xp(owner, 25)

    return jsonify({"done": new_log, "player": get_player_state(owner)})

def _csv_safe(value):
    # Prefix values that would otherwise be interpreted as a formula by
    # Excel/Sheets (e.g. a habit named "=cmd|...") so they render as plain text.
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value

@app.route("/export/csv")
def export_csv():
    import csv
    import io
    from flask import Response

    with db_connection() as conn:
        rows = conn.execute("""
            SELECT h.name AS habit, l.logged_date AS date
            FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE h.owner = ?
            ORDER BY h.name, l.logged_date
        """, (session["uid"],)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Habit", "Date"])
    for row in rows:
        writer.writerow([_csv_safe(row["habit"]), row["date"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=habits.csv"}
    )

@app.route("/log/<int:habit_id>", methods=["POST"])
@rate_limit(limit=90)  # highest of any route - checking off a long habit list every day is normal use, not abuse
def log_habit(habit_id):
    owner = session["uid"]
    today = get_local_today()

    with db_connection() as conn:
        owned = conn.execute("SELECT id FROM habits WHERE id = ? AND owner = ? AND archived_at IS NULL", (habit_id, owner)).fetchone()
        if not owned:
            return jsonify({"done": False}), 404

        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO logs (habit_id, logged_date) VALUES (?, ?)",
            (habit_id, today.isoformat())
        )
        conn.commit()
        new_log = cursor.rowcount > 0

        today_weekday = today.weekday()
        all_habits = conn.execute("SELECT id, repeat_days FROM habits WHERE owner = ? AND archived_at IS NULL", (owner,)).fetchall()
        scheduled_ids = {h["id"] for h in all_habits if (h["repeat_days"] or "1111111")[today_weekday] == "1"}
        logged_today_ids = {row["habit_id"] for row in conn.execute("""
            SELECT l.habit_id FROM logs l
            JOIN habits h ON l.habit_id = h.id
            WHERE l.logged_date = ? AND h.owner = ?
        """, (today.isoformat(), owner)).fetchall()}

    # Only count/require habits actually scheduled today - a log left over on a
    # habit whose schedule later changed (or one logged directly, bypassing the
    # Today list) must not pad this or let the perfect-day bonus fire early.
    logged_count = len(logged_today_ids & scheduled_ids)

    if new_log:
        add_xp(owner, 10)
        if scheduled_ids and scheduled_ids.issubset(logged_today_ids):
            add_xp(owner, 25)

    return jsonify({
        "done": new_log,
        "logged_count": logged_count,
        "player": get_player_state(owner)
    })

if __name__ == "__main__":
    init_db()
    app.run(debug=not IS_PRODUCTION)
