from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "trublu.db"
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = "local-dev-secret-change-me"
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)


ROLE_RANK = {"worker": 1, "admin": 2, "owner": 3}


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS rate_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            container_type TEXT UNIQUE NOT NULL,
            pay_rate REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pay_period_lock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT UNIQUE NOT NULL,
            is_locked INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS container_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            container_type TEXT NOT NULL,
            container_count INTEGER NOT NULL,
            notes TEXT,
            issue_text TEXT,
            photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            approved_by INTEGER,
            approved_at TEXT,
            submitted_at TEXT NOT NULL,
            calculated_pay REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(worker_id) REFERENCES users(id),
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(approved_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS toolbox_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            notes TEXT,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY(worker_id) REFERENCES users(id)
        );
        """
    )

    users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if users == 0:
        seed_users = [
            ("worker1", generate_password_hash("worker123"), "worker", "Worker One"),
            ("admin1", generate_password_hash("admin123"), "admin", "Admin User"),
            ("owner1", generate_password_hash("owner123"), "owner", "Owner User"),
        ]
        db.executemany(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)",
            seed_users,
        )

    sites = db.execute("SELECT COUNT(*) AS c FROM sites").fetchone()["c"]
    if sites == 0:
        db.executemany(
            "INSERT INTO sites (name) VALUES (?)",
            [("Port Yard A",), ("Port Yard B",), ("Warehouse 7",)],
        )

    rates = db.execute("SELECT COUNT(*) AS c FROM rate_table").fetchone()["c"]
    if rates == 0:
        db.executemany(
            "INSERT INTO rate_table (container_type, pay_rate) VALUES (?, ?)",
            [("20ft", 25.0), ("40ft", 40.0), ("Mixed", 30.0)],
        )
    db.commit()
    db.close()


def role_required(min_role: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if ROLE_RANK.get(session["role"], 0) < ROLE_RANK[min_role]:
                flash("You do not have access to this page.", "danger")
                return redirect(url_for("dashboard"))
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def current_week_start(dt: datetime) -> datetime:
    return dt - timedelta(days=dt.weekday())


def is_week_locked(week_start_iso: str) -> bool:
    row = get_db().execute(
        "SELECT is_locked FROM pay_period_lock WHERE week_start = ?", (week_start_iso,)
    ).fetchone()
    return bool(row and row["is_locked"] == 1)


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@role_required("worker")
def dashboard():
    db = get_db()
    role = session["role"]
    if role == "worker":
        jobs = db.execute(
            """
            SELECT j.*, s.name AS site_name
            FROM container_jobs j
            JOIN sites s ON j.site_id = s.id
            WHERE j.worker_id = ?
            ORDER BY j.submitted_at DESC
            """,
            (session["user_id"],),
        ).fetchall()
        toolbox = db.execute(
            "SELECT * FROM toolbox_meetings WHERE worker_id = ? ORDER BY submitted_at DESC",
            (session["user_id"],),
        ).fetchall()
        return render_template("worker_dashboard.html", jobs=jobs, toolbox=toolbox)

    jobs = db.execute(
        """
        SELECT j.*, s.name AS site_name, u.full_name AS worker_name
        FROM container_jobs j
        JOIN sites s ON j.site_id = s.id
        JOIN users u ON u.id = j.worker_id
        ORDER BY j.submitted_at DESC
        """
    ).fetchall()
    toolbox = db.execute(
        """
        SELECT t.*, u.full_name AS worker_name
        FROM toolbox_meetings t
        JOIN users u ON u.id = t.worker_id
        ORDER BY t.submitted_at DESC
        """
    ).fetchall()
    summary = db.execute(
        """
        SELECT u.full_name, COALESCE(SUM(j.calculated_pay), 0) AS total_pay, COUNT(j.id) AS jobs
        FROM users u
        LEFT JOIN container_jobs j ON j.worker_id = u.id
        WHERE u.role = 'worker'
        GROUP BY u.id
        ORDER BY u.full_name
        """
    ).fetchall()
    return render_template("admin_dashboard.html", jobs=jobs, toolbox=toolbox, summary=summary)


@app.route("/jobs/new", methods=["GET", "POST"])
@role_required("worker")
def new_job():
    db = get_db()
    sites = db.execute("SELECT * FROM sites WHERE active = 1 ORDER BY name").fetchall()
    rate_types = db.execute("SELECT container_type FROM rate_table ORDER BY container_type").fetchall()
    if request.method == "POST":
        submitted_at = datetime.utcnow()
        week_start_iso = current_week_start(submitted_at).date().isoformat()
        if is_week_locked(week_start_iso):
            flash("Current pay period is locked. Contact owner.", "danger")
            return redirect(url_for("dashboard"))

        site_id = int(request.form["site_id"])
        container_type = request.form["container_type"]
        container_count = int(request.form["container_count"])
        notes = request.form.get("notes", "")
        issue_text = request.form.get("issue_text", "")

        rate = db.execute(
            "SELECT pay_rate FROM rate_table WHERE container_type = ?", (container_type,)
        ).fetchone()
        pay = (rate["pay_rate"] if rate else 0) * container_count

        photo_path = None
        file = request.files.get("photo")
        if file and file.filename:
            if allowed_file(file.filename):
                UPLOAD_DIR.mkdir(exist_ok=True)
                safe_name = secure_filename(file.filename)
                stamped = f"{int(datetime.utcnow().timestamp())}_{safe_name}"
                file.save(UPLOAD_DIR / stamped)
                photo_path = stamped
            else:
                flash("Unsupported file format.", "danger")
                return redirect(url_for("new_job"))

        db.execute(
            """
            INSERT INTO container_jobs
            (worker_id, site_id, container_type, container_count, notes, issue_text, photo_path, submitted_at, calculated_pay)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                site_id,
                container_type,
                container_count,
                notes,
                issue_text,
                photo_path,
                submitted_at.isoformat(),
                pay,
            ),
        )
        db.commit()
        flash("Container job submitted.", "success")
        return redirect(url_for("dashboard"))
    return render_template("new_job.html", sites=sites, rate_types=rate_types)


@app.route("/toolbox/new", methods=["GET", "POST"])
@role_required("worker")
def new_toolbox():
    if request.method == "POST":
        get_db().execute(
            "INSERT INTO toolbox_meetings (worker_id, topic, notes, submitted_at) VALUES (?, ?, ?, ?)",
            (
                session["user_id"],
                request.form["topic"],
                request.form.get("notes", ""),
                datetime.utcnow().isoformat(),
            ),
        )
        get_db().commit()
        flash("Toolbox meeting record submitted.", "success")
        return redirect(url_for("dashboard"))
    return render_template("new_toolbox.html")


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
@role_required("admin")
def set_status(job_id: int):
    status = request.form["status"]
    if status not in {"approved", "rejected", "pending"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("dashboard"))
    get_db().execute(
        "UPDATE container_jobs SET status = ?, approved_by = ?, approved_at = ? WHERE id = ?",
        (status, session["user_id"], datetime.utcnow().isoformat(), job_id),
    )
    get_db().commit()
    return redirect(url_for("dashboard"))


@app.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_job(job_id: int):
    if session["role"] != "owner":
        flash("Only owner can edit entries.", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    job = db.execute("SELECT * FROM container_jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        flash("Job not found.", "danger")
        return redirect(url_for("dashboard"))
    sites = db.execute("SELECT * FROM sites ORDER BY name").fetchall()
    rates = db.execute("SELECT container_type, pay_rate FROM rate_table").fetchall()

    if request.method == "POST":
        site_id = int(request.form["site_id"])
        container_type = request.form["container_type"]
        container_count = int(request.form["container_count"])
        notes = request.form.get("notes", "")
        issue_text = request.form.get("issue_text", "")

        rate = db.execute(
            "SELECT pay_rate FROM rate_table WHERE container_type = ?", (container_type,)
        ).fetchone()
        pay = (rate["pay_rate"] if rate else 0) * container_count

        db.execute(
            """
            UPDATE container_jobs
            SET site_id = ?, container_type = ?, container_count = ?, notes = ?, issue_text = ?, calculated_pay = ?
            WHERE id = ?
            """,
            (site_id, container_type, container_count, notes, issue_text, pay, job_id),
        )
        db.commit()
        flash("Job updated.", "success")
        return redirect(url_for("dashboard"))

    return render_template("edit_job.html", job=job, sites=sites, rates=rates)


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@role_required("owner")
def delete_job(job_id: int):
    get_db().execute("DELETE FROM container_jobs WHERE id = ?", (job_id,))
    get_db().commit()
    flash("Job deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/owner/settings", methods=["GET", "POST"])
@role_required("owner")
def owner_settings():
    db = get_db()
    if request.method == "POST":
        action = request.form["action"]
        if action == "add_staff":
            db.execute(
                "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)",
                (
                    request.form["username"],
                    generate_password_hash(request.form["password"]),
                    request.form["role"],
                    request.form["full_name"],
                ),
            )
        elif action == "add_site":
            db.execute("INSERT INTO sites (name) VALUES (?)", (request.form["site_name"],))
        elif action == "add_rate":
            db.execute(
                "INSERT OR REPLACE INTO rate_table (container_type, pay_rate) VALUES (?, ?)",
                (request.form["container_type"], float(request.form["pay_rate"])),
            )
        elif action == "lock_week":
            week_start = request.form["week_start"]
            db.execute(
                "INSERT INTO pay_period_lock (week_start, is_locked) VALUES (?, 1) ON CONFLICT(week_start) DO UPDATE SET is_locked = 1",
                (week_start,),
            )
        db.commit()
        return redirect(url_for("owner_settings"))

    staff = db.execute("SELECT id, full_name, username, role, active FROM users ORDER BY role, full_name").fetchall()
    sites = db.execute("SELECT * FROM sites ORDER BY name").fetchall()
    rates = db.execute("SELECT * FROM rate_table ORDER BY container_type").fetchall()
    locks = db.execute("SELECT * FROM pay_period_lock ORDER BY week_start DESC").fetchall()
    return render_template("owner_settings.html", staff=staff, sites=sites, rates=rates, locks=locks)


@app.route("/weekly-summary")
@role_required("admin")
def weekly_summary():
    week_start = request.args.get("week_start")
    if not week_start:
        week_start = current_week_start(datetime.utcnow()).date().isoformat()
    week_start_dt = datetime.fromisoformat(week_start)
    week_end = (week_start_dt + timedelta(days=6)).date().isoformat()
    db = get_db()
    rows = db.execute(
        """
        SELECT u.full_name, COALESCE(SUM(j.calculated_pay), 0) AS total_pay,
               COUNT(j.id) AS total_jobs,
               SUM(CASE WHEN j.status = 'approved' THEN 1 ELSE 0 END) AS approved_jobs
        FROM users u
        LEFT JOIN container_jobs j
            ON u.id = j.worker_id
           AND DATE(j.submitted_at) BETWEEN ? AND ?
        WHERE u.role = 'worker'
        GROUP BY u.id
        ORDER BY u.full_name
        """,
        (week_start, week_end),
    ).fetchall()
    return render_template("weekly_summary.html", rows=rows, week_start=week_start, week_end=week_end)


@app.route("/uploads/<path:filename>")
@role_required("admin")
def uploaded_file(filename: str):
    return send_file(UPLOAD_DIR / filename)


if __name__ == "__main__":
    init_db()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    app.run(debug=True)
