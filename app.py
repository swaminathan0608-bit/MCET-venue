"""
MCET Smart Hall & Venue Availability Management System
Dr. Mahalingam College of Engineering and Technology, Pollachi

Run:   python app.py      then open  http://127.0.0.1:5000

Three logins: Student, Hall Supervisor, Admin.
"""
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db as database
from db import get_db

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)

_key_file = Path(__file__).with_name(".secret_key")
if not _key_file.exists():
    _key_file.write_text(secrets.token_hex(32))
app.secret_key = os.environ.get("SECRET_KEY") or _key_file.read_text().strip()
app.teardown_appcontext(database.close_db)

COLLEGE = {
    "name": "Dr. Mahalingam College of Engineering and Technology",
    "short": "MCET",
    "place": "Pollachi, Coimbatore District",
    "app": "Smart Hall & Venue Availability",
}

ROLE_META = {
    "student": {
        "label": "Student", "home": "student_dashboard", "icon": "🎓",
        "id_label": "College email or roll number",
        "blurb": "Check which halls are free, request a venue for your club or department event, and track approval.",
        "points": ["See free halls by date & time", "Request a venue for an event", "Track approval status"],
    },
    "supervisor": {
        "label": "Hall Supervisor", "home": "supervisor_dashboard", "icon": "📋",
        "id_label": "Staff ID",
        "blurb": "Approve or reject requests for the halls you look after, block slots for maintenance, see the schedule.",
        "points": ["Approve or reject requests", "Block slots for maintenance", "Schedule of your halls"],
    },
    "admin": {
        "label": "Admin", "home": "admin_dashboard", "icon": "🛡️",
        "id_label": "Admin ID",
        "blurb": "Manage every venue, supervisor and booking across departments, resolve conflicts and see utilisation.",
        "points": ["Add & manage all venues", "Create supervisor accounts", "Utilisation reports & Excel export"],
    },
}

VENUE_TYPES = {
    "AUDITORIUM": "Auditorium",
    "SEMINAR_HALL": "Seminar hall",
    "CONFERENCE_ROOM": "Conference room",
    "LAB": "Laboratory",
    "OUTDOOR": "Outdoor venue",
    "SPORTS": "Sports venue",
    "CLASSROOM": "Classroom",
}

STATUS_LABEL = {
    "PENDING": "Pending", "APPROVED": "Approved", "REJECTED": "Rejected",
    "CANCELLED": "Cancelled", "BLOCKED": "Blocked",
}
ACTIVE = ("PENDING", "APPROVED", "BLOCKED")
ALLOTTED = ("APPROVED", "BLOCKED")


class BookingError(Exception):
    """A booking rule was broken; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_str():
    return date.today().isoformat()


def parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return default or today_str()


def add_days(d, n):
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def to_min(hhmm):
    h, m = hhmm[:5].split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def valid_time(s):
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except (TypeError, ValueError):
        return False


@app.template_filter("nicedate")
def nicedate(s):
    if not s:
        return "—"
    d = datetime.strptime(s[:10], "%Y-%m-%d")
    return d.strftime("%a, %d %b %Y")


@app.template_filter("longdate")
def longdate(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%A, %d %B %Y")


@app.template_filter("t12")
def t12(s):
    """'2026-11-03 14:30' or '14:30' -> '02:30 PM'"""
    if not s:
        return ""
    hhmm = s[-5:]
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p")


@app.context_processor
def inject_globals():
    return {
        "COLLEGE": COLLEGE, "ROLE_META": ROLE_META, "VENUE_TYPES": VENUE_TYPES,
        "STATUS_LABEL": STATUS_LABEL, "me": g.get("user"), "csrf_token": csrf_token, "today": today_str(),
    }


# --- CSRF protection for every form ---------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        token = request.form.get("_csrf") or request.headers.get("X-CSRF")
        if not token or token != session.get("_csrf"):
            abort(400, "Form expired — please go back, reload the page and try again.")


def safe_back(url):
    """Only allow redirects back to pages on this site."""
    return url if url and url.startswith("/") and not url.startswith("//") else ""


def login_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                role = roles[0] if roles else "student"
                return redirect(url_for("login", role=role, next=request.path))
            if roles and g.user["role"] not in roles:
                flash("That page is not available for your role.", "error")
                return redirect(url_for(ROLE_META[g.user["role"]]["home"]))
            return view(*args, **kwargs)
        return wrapped
    return deco


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def venues(active_only=True):
    sql = "SELECT v.*, u.full_name AS supervisor_name FROM venues v LEFT JOIN users u ON u.id = v.supervisor_id"
    if active_only:
        sql += " WHERE v.is_active = 1"
    return get_db().execute(sql + " ORDER BY v.name").fetchall()


def departments():
    return get_db().execute("SELECT * FROM departments ORDER BY sort_order").fetchall()


BOOKING_SELECT = """
SELECT b.*, v.name AS venue_name, v.code AS venue_code, v.capacity AS venue_capacity, v.supervisor_id,
       u.full_name AS requester_name, u.login_id AS requester_login, u.role AS requester_role,
       d.full_name AS decided_by_name
FROM bookings b
JOIN venues v ON v.id = b.venue_id
LEFT JOIN users u ON u.id = b.requested_by
LEFT JOIN users d ON d.id = b.decided_by
"""


def bookings_between(start, end, statuses=None, venue_ids=None, user_id=None, order="ASC"):
    sql = BOOKING_SELECT + " WHERE b.start_at < ? AND b.end_at > ?"
    args = [end, start]
    if statuses:
        sql += f" AND b.status IN ({','.join('?' * len(statuses))})"
        args += list(statuses)
    if venue_ids is not None:
        if not venue_ids:
            return []
        sql += f" AND b.venue_id IN ({','.join('?' * len(venue_ids))})"
        args += list(venue_ids)
    if user_id:
        sql += " AND b.requested_by = ?"
        args.append(user_id)
    sql += f" ORDER BY b.start_at {order}"
    return get_db().execute(sql, args).fetchall()


def get_booking(bid):
    return get_db().execute(BOOKING_SELECT + " WHERE b.id = ?", (bid,)).fetchone()


def can_manage(user, venue_row):
    """Admins manage every venue; supervisors manage the venues assigned to them."""
    if not user or not venue_row:
        return False
    return user["role"] == "admin" or (
        user["role"] == "supervisor" and venue_row["supervisor_id"] == user["id"]
    )


def overlapping(venue_id, start, end, statuses, exclude_id=None):
    sql = (BOOKING_SELECT + " WHERE b.venue_id = ? AND b.start_at < ? AND b.end_at > ?"
           f" AND b.status IN ({','.join('?' * len(statuses))})")
    args = [venue_id, end, start, *statuses]
    if exclude_id:
        sql += " AND b.id <> ?"
        args.append(exclude_id)
    return get_db().execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# Booking rules (the heart of conflict avoidance)
# ---------------------------------------------------------------------------
def check_request(user, venue, day, start, end, attendees, kind="request"):
    """Return (problems, allotted_clashes, pending_overlaps, alternatives) for a proposed slot."""
    problems = []
    if not venue:
        return ["Choose a venue."], [], [], []
    if not (valid_time(start) and valid_time(end)):
        return ["Enter a valid start and end time."], [], [], []
    s, e = f"{day} {start}", f"{day} {end}"
    if e <= s:
        problems.append("End time must be after start time.")
    if not venue["is_active"]:
        problems.append(f"{venue['name']} is not open for booking.")
    if kind == "request":
        if attendees > venue["capacity"]:
            problems.append(f"{venue['name']} holds {venue['capacity']} people; you entered {attendees}.")
        if user["role"] == "student" and (start < venue["open_time"] or end > venue["close_time"]):
            problems.append(f"{venue['name']} is open {venue['open_time']} – {venue['close_time']} only.")
    if user["role"] != "admin" and s < now_str():
        problems.append("That time has already passed.")

    clashes = overlapping(venue["id"], s, e, ALLOTTED) if e > s else []
    pending = overlapping(venue["id"], s, e, ("PENDING",)) if e > s else []

    alternatives = []
    if kind == "request" and e > s:
        busy = {r["venue_id"] for r in bookings_between(s, e, ALLOTTED)}
        for v in venues():
            if (v["id"] != venue["id"] and v["id"] not in busy and v["capacity"] >= attendees
                    and v["open_time"] <= start and end <= v["close_time"]):
                alternatives.append(v)
        alternatives = sorted(alternatives, key=lambda v: v["capacity"])[:4]
    return problems, clashes, pending, alternatives


def create_booking(user, form):
    conn = get_db()
    kind = "block" if form.get("kind") == "block" else "request"
    venue = conn.execute("SELECT * FROM venues WHERE id = ?", (form.get("venue_id"),)).fetchone()
    day = parse_date(form.get("date"))
    start, end = form.get("start", ""), form.get("end", "")
    title = (form.get("event_title") or "").strip()
    try:
        attendees = max(1, int(form.get("attendees") or 1))
    except ValueError:
        attendees = 1
    if kind == "block":
        attendees = 1

    if len(title) < 3:
        raise BookingError("Please enter an event title (at least 3 characters).")
    manage = can_manage(user, venue)
    if kind == "block" and not manage:
        raise BookingError("Only the hall supervisor or admin can block a slot.")
    if user["role"] == "student" and not (form.get("faculty_incharge") or "").strip():
        raise BookingError("Please name the faculty in-charge for your event.")

    problems, clashes, _pending, _alt = check_request(user, venue, day, start, end, attendees, kind)
    if problems:
        raise BookingError(" ".join(problems))
    if clashes:
        c = clashes[0]
        raise BookingError(
            f"Not available: {c['booking_no']} “{c['event_title']}” has {t12(c['start_at'])} – {t12(c['end_at'])}."
        )

    s, e = f"{day} {start}", f"{day} {end}"
    if kind == "block":
        status = "BLOCKED"
    elif form.get("direct") == "1" and manage:
        status = "APPROVED"
    else:
        status = "PENDING"
        dup = conn.execute(
            "SELECT 1 FROM bookings WHERE venue_id=? AND requested_by=? AND status='PENDING' "
            "AND start_at < ? AND end_at > ?", (venue["id"], user["id"], e, s)).fetchone()
        if dup:
            raise BookingError("You already have a pending request for this slot.")

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO bookings (venue_id, requested_by, department, event_title, purpose, attendees, "
            "faculty_incharge, start_at, end_at, status, decided_by, decided_at, decision_note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (venue["id"], user["id"], form.get("department") or user["department"], title,
             (form.get("purpose") or "").strip() or None, attendees,
             (form.get("faculty_incharge") or "").strip() or None, s, e, status,
             None if status == "PENDING" else user["id"], None if status == "PENDING" else now_str(),
             "Direct allotment by staff" if status == "APPROVED" else None),
        )
        bid = cur.lastrowid
        if status in ALLOTTED:
            _auto_reject_overlaps(bid, user["id"])
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        if "SLOT_CONFLICT" in str(exc):
            raise BookingError("That slot was just allotted to someone else. Pick another time or venue.") from exc
        raise BookingError(str(exc)) from exc
    return get_booking(bid)


def _auto_reject_overlaps(bid, actor_id):
    """When a slot is allotted, decline every overlapping pending request with a clear reason."""
    conn = get_db()
    b = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.execute(
        "UPDATE bookings SET status='REJECTED', decided_by=?, decided_at=?, "
        "decision_note='Auto-rejected: slot allotted to ' || ? || ' (' || ? || ')' "
        "WHERE venue_id=? AND id<>? AND status='PENDING' AND start_at < ? AND end_at > ?",
        (actor_id, now_str(), b["booking_no"], b["event_title"], b["venue_id"], bid, b["end_at"], b["start_at"]),
    )


def change_status(user, booking, action, note=""):
    """approve / reject / cancel with permission checks. Returns a message."""
    conn = get_db()
    venue = conn.execute("SELECT * FROM venues WHERE id = ?", (booking["venue_id"],)).fetchone()
    manage = can_manage(user, venue)
    mine = booking["requested_by"] == user["id"]
    st = booking["status"]

    if action in ("approve", "reject"):
        if not manage:
            raise BookingError("Only the hall supervisor of this venue or an admin can decide.")
        if st != "PENDING" and user["role"] != "admin":
            raise BookingError("This request was already decided.")
        new = "APPROVED" if action == "approve" else "REJECTED"
    elif action == "cancel":
        if not (manage or mine):
            raise BookingError("You cannot cancel this booking.")
        if st not in ACTIVE:
            raise BookingError("Only pending, approved or blocked bookings can be cancelled.")
        if not manage and booking["end_at"] < now_str():
            raise BookingError("This booking is already over.")
        new = "CANCELLED"
    else:
        raise BookingError("Unknown action.")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE bookings SET status=?, decided_by=?, decided_at=?, "
            "decision_note=COALESCE(NULLIF(?, ''), decision_note) WHERE id=?",
            (new, user["id"] if (manage or new != "CANCELLED") else booking["decided_by"], now_str(),
             note.strip(), booking["id"]),
        )
        rejected = 0
        if new == "APPROVED":
            before = conn.total_changes
            _auto_reject_overlaps(booking["id"], user["id"])
            rejected = conn.total_changes - before
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        if "SLOT_CONFLICT" in str(exc):
            raise BookingError("Cannot approve: this would overlap a slot that is already allotted.") from exc
        raise
    msg = f"{booking['booking_no']} {new.lower()}."
    if rejected:
        msg += f" {rejected} overlapping request(s) were declined automatically."
    return msg


# ---------------------------------------------------------------------------
# Availability grid helper
# ---------------------------------------------------------------------------
def build_grid(day, venue_list, booking_list, me_id=None):
    if not venue_list:
        return None
    day_start = min([8 * 60] + [to_min(v["open_time"]) for v in venue_list])
    day_end = max([18 * 60] + [to_min(v["close_time"]) for v in venue_list])
    span = day_end - day_start

    def pct(m):
        return round((m - day_start) / span * 100, 3)

    def hour_label(m):
        h = m // 60
        return f"{12 if h % 12 == 0 else h % 12}{'pm' if h >= 12 else 'am'}"

    hours = [{"label": hour_label(m), "left": pct(m)} for m in range(day_start, day_end + 1, 60)]
    rows = []
    for v in venue_list:
        items = []
        lane_ends = []
        for b in sorted([b for b in booking_list if b["venue_id"] == v["id"] and b["status"] in ACTIVE],
                        key=lambda b: b["start_at"]):
            s = max(day_start, to_min(b["start_at"][11:]) if b["start_at"][:10] == day else day_start)
            e = min(day_end, to_min(b["end_at"][11:]) if b["end_at"][:10] == day else day_end)
            if e <= s:
                continue
            lane = 0
            if b["status"] == "PENDING":
                lane = next((i for i, end in enumerate(lane_ends) if end <= b["start_at"]), len(lane_ends))
                if lane == len(lane_ends):
                    lane_ends.append(b["end_at"])
                else:
                    lane_ends[lane] = b["end_at"]
            items.append({"b": b, "left": pct(s), "width": pct(e) - pct(s), "lane": lane,
                          "mine": me_id is not None and b["requested_by"] == me_id})
        lanes = max(1, len(lane_ends))
        o, c = to_min(v["open_time"]), to_min(v["close_time"])
        rows.append({"v": v, "slots": items, "lanes": lanes, "height": max(56, lanes * 26 + 8),
                     "closed_before": pct(o) if o > day_start else 0,
                     "closed_after": pct(c) if c < day_end else 100})
    now_pct = None
    if day == today_str():
        n = datetime.now().hour * 60 + datetime.now().minute
        if day_start < n < day_end:
            now_pct = pct(n)
    return {"hours": hours, "rows": rows, "day_start": day_start, "day_end": day_end, "now": now_pct, "date": day}


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html", venues=venues())


@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):
    if role not in ROLE_META:
        abort(404)
    error = None
    if request.method == "POST":
        ident = (request.form.get("ident") or "").strip().lower()
        pw = request.form.get("password") or ""
        user = get_db().execute(
            "SELECT * FROM users WHERE lower(login_id) = ? OR lower(email) = ?", (ident, ident)
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], pw):
            error = "Incorrect ID or password."
        elif user["role"] != role:
            label = ROLE_META[user["role"]]["label"]
            error = f"This account is registered as {label}. Please use the {label} login."
        else:
            session.clear()
            session["uid"] = user["id"]
            nxt = safe_back(request.args.get("next"))
            if nxt:
                return redirect(nxt)
            return redirect(url_for(ROLE_META[role]["home"]))
    return render_template("login.html", role=role, meta=ROLE_META[role], error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    f = request.form
    if request.method == "POST":
        name, roll, email = f.get("full_name", "").strip(), f.get("roll_no", "").strip().lower(), \
            f.get("email", "").strip().lower()
        pw, pw2 = f.get("password", ""), f.get("password2", "")
        if len(name) < 2 or not roll or "@" not in email:
            error = "Please fill in your name, roll number and a valid email."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != pw2:
            error = "Passwords do not match."
        else:
            try:
                cur = get_db().execute(
                    "INSERT INTO users (role, full_name, login_id, email, password_hash, department, phone) "
                    "VALUES ('student', ?, ?, ?, ?, ?, ?)",
                    (name, roll, email, generate_password_hash(pw), f.get("department") or None,
                     f.get("phone", "").strip() or None),
                )
                session.clear()
                session["uid"] = cur.lastrowid
                flash("Welcome! Your student account is ready.", "success")
                return redirect(url_for("student_dashboard"))
            except sqlite3.IntegrityError:
                error = "That roll number or email is already registered."
    return render_template("register.html", departments=departments(), error=error, form=f)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required()
def change_password():
    error = None
    if request.method == "POST":
        cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "new2"))
        if not check_password_hash(g.user["password_hash"], cur):
            error = "Current password is wrong."
        elif len(new) < 8:
            error = "New password must be at least 8 characters."
        elif new != new2:
            error = "New passwords do not match."
        else:
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
            flash("Password changed.", "success")
            return redirect(url_for(ROLE_META[g.user["role"]]["home"]))
    return render_template("password.html", error=error)


# ---------------------------------------------------------------------------
# Availability + booking (all signed-in roles)
# ---------------------------------------------------------------------------
@app.route("/availability")
@login_required()
def availability():
    day = parse_date(request.args.get("date"))
    vtype = request.args.get("type", "")
    min_cap = int(request.args.get("cap") or 0)
    q = (request.args.get("q") or "").strip().lower()
    all_v = venues()
    day_bookings = bookings_between(f"{day} 00:00", f"{add_days(day, 1)} 00:00", ACTIVE)
    shown = [v for v in all_v
             if (not vtype or v["venue_type"] == vtype) and v["capacity"] >= min_cap
             and (not q or q in f"{v['name']} {v['block']} {v['code']}".lower())]
    grid = build_grid(day, shown, day_bookings, g.user["id"])
    stats = {
        "booked": sum(1 for b in day_bookings if b["status"] == "APPROVED"),
        "pending": sum(1 for b in day_bookings if b["status"] == "PENDING"),
    }
    return render_template("availability.html", grid=grid, day=day, prev=add_days(day, -1), next=add_days(day, 1),
                           stats=stats, venue_count=len(all_v), vtype=vtype, min_cap=min_cap, q=q)


@app.route("/request", methods=["GET", "POST"])
@login_required()
def request_venue():
    kind = request.values.get("kind", "request")
    all_v = venues()
    if g.user["role"] == "supervisor":
        managed = [v for v in all_v if v["supervisor_id"] == g.user["id"]]
        if kind == "block":
            all_v = managed
    if kind == "block" and g.user["role"] == "student":
        abort(403)
    error = None
    if request.method == "POST":
        try:
            b = create_booking(g.user, request.form)
            flash({"PENDING": f"{b['booking_no']} submitted — waiting for the hall supervisor's approval.",
                   "APPROVED": f"{b['booking_no']}: slot allotted.",
                   "BLOCKED": f"{b['booking_no']}: slot blocked."}[b["status"]], "success")
            return redirect(url_for("availability", date=b["start_at"][:10]))
        except BookingError as exc:
            error = str(exc)
        form = request.form
    else:
        start = request.args.get("start", "10:00")
        start = start if valid_time(start) else "10:00"
        end = min_to_hhmm(min(to_min(start) + 60, 23 * 60 + 30))
        form = {"venue_id": request.args.get("venue", str(all_v[0]["id"]) if all_v else ""),
                "date": parse_date(request.args.get("date")), "start": start, "end": end,
                "attendees": "1" if kind == "block" else "50",
                "event_title": "Maintenance" if kind == "block" else "", "department": g.user["department"] or ""}
    managed_ids = [v["id"] for v in all_v if can_manage(g.user, v)]
    return render_template("request.html", venues=all_v, departments=departments(), form=form, kind=kind,
                           error=error, managed_ids=managed_ids)


@app.route("/api/check")
@login_required()
def api_check():
    """Live availability check used by the request form while the user types."""
    venue = get_db().execute("SELECT * FROM venues WHERE id = ?", (request.args.get("venue_id"),)).fetchone()
    day = parse_date(request.args.get("date"))
    try:
        attendees = max(1, int(request.args.get("attendees") or 1))
    except ValueError:
        attendees = 1
    kind = request.args.get("kind", "request")
    problems, clashes, pending, alts = check_request(
        g.user, venue, day, request.args.get("start", ""), request.args.get("end", ""), attendees, kind)
    return jsonify({
        "problems": problems,
        "clashes": [f"{c['booking_no']} “{c['event_title']}” {t12(c['start_at'])} – {t12(c['end_at'])}"
                    for c in clashes],
        "pending": len(pending),
        "alternatives": [{"id": a["id"], "name": a["name"], "capacity": a["capacity"]} for a in alts],
        "venue": venue["name"] if venue else "",
        "facilities": venue["facilities"] if venue else "",
        "can_manage": can_manage(g.user, venue),
    })


@app.route("/booking/<int:bid>", methods=["GET", "POST"])
@login_required()
def booking_detail(bid):
    b = get_booking(bid)
    if not b:
        abort(404)
    if request.method == "POST":
        try:
            flash(change_status(g.user, b, request.form.get("action", ""), request.form.get("note", "")), "success")
        except BookingError as exc:
            flash(str(exc), "error")
        return redirect(safe_back(request.form.get("back")) or url_for("booking_detail", bid=bid))
    venue = get_db().execute("SELECT * FROM venues WHERE id = ?", (b["venue_id"],)).fetchone()
    manage = can_manage(g.user, venue)
    mine = b["requested_by"] == g.user["id"]
    rivals = overlapping(b["venue_id"], b["start_at"], b["end_at"], ("PENDING",), exclude_id=b["id"]) \
        if b["status"] == "PENDING" else []
    return render_template("booking.html", b=b, venue=venue, manage=manage, mine=mine, rivals=rivals,
                           upcoming=b["end_at"] > now_str(), back=safe_back(request.args.get("back", "")))


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------
@app.route("/student")
@login_required("student")
def student_dashboard():
    tab = request.args.get("tab", "upcoming")
    mine = get_db().execute(BOOKING_SELECT + " WHERE b.requested_by = ? ORDER BY b.start_at DESC",
                            (g.user["id"],)).fetchall()
    now = now_str()
    upcoming = sorted([b for b in mine if b["status"] == "APPROVED" and b["end_at"] > now],
                      key=lambda b: b["start_at"])
    pending = [b for b in mine if b["status"] == "PENDING"]
    history = [b for b in mine if b not in upcoming and b not in pending]
    rows = {"upcoming": upcoming, "pending": pending, "history": history}.get(tab, upcoming)
    return render_template("student.html", tab=tab, rows=rows, upcoming=upcoming, pending=pending,
                           history=history, total=len(mine))


# ---------------------------------------------------------------------------
# Hall supervisor
# ---------------------------------------------------------------------------
@app.route("/supervisor")
@login_required("supervisor")
def supervisor_dashboard():
    day = parse_date(request.args.get("date"))
    mine = [v for v in venues(active_only=False) if v["supervisor_id"] == g.user["id"]]
    ids = [v["id"] for v in mine]
    now = now_str()
    upcoming = bookings_between(now, "9999-12-31 23:59", ACTIVE, venue_ids=ids)
    pending = [b for b in upcoming if b["status"] == "PENDING"]
    allotted = [b for b in upcoming if b["status"] != "PENDING"]
    today = today_str()
    todays = bookings_between(f"{today} 00:00", f"{add_days(today, 1)} 00:00", ("APPROVED",), venue_ids=ids)
    day_b = bookings_between(f"{day} 00:00", f"{add_days(day, 1)} 00:00", ACTIVE, venue_ids=ids)
    rivals = {
        b["id"]: [o for o in pending if o["id"] != b["id"] and o["venue_id"] == b["venue_id"]
                  and o["start_at"] < b["end_at"] and b["start_at"] < o["end_at"]]
        for b in pending
    }
    return render_template("supervisor.html", my_venues=mine, pending=pending, allotted=allotted,
                           today_count=len(todays), grid=build_grid(day, [v for v in mine if v["is_active"]], day_b),
                           day=day, prev=add_days(day, -1), next=add_days(day, 1), rivals=rivals)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    tab = request.args.get("tab", "overview")
    conn = get_db()
    ctx = {"tab": tab}
    if tab == "overview":
        today = today_str()
        all_v = venues()
        todays = bookings_between(f"{today} 00:00", f"{add_days(today, 1)} 00:00", ACTIVE)
        pending = bookings_between(now_str(), "9999-12-31 23:59", ("PENDING",))
        contested = [b for b in pending if any(
            o["id"] != b["id"] and o["venue_id"] == b["venue_id"] and o["start_at"] < b["end_at"]
            and b["start_at"] < o["end_at"] for o in pending)]
        soon = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
        ctx.update(
            venue_count=len(all_v), pending=pending, contested=contested,
            stale=[b for b in pending if b["start_at"] < soon],
            unassigned=[v for v in all_v if not v["supervisor_id"]],
            today_events=sum(1 for b in todays if b["status"] == "APPROVED"),
            students=conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0],
            grid=build_grid(today, all_v, todays),
            oldest=sorted(pending, key=lambda b: b["created_at"])[:10],
        )
    elif tab == "requests":
        status = request.args.get("status", "PENDING")
        vid = request.args.get("venue", "")
        dfrom = parse_date(request.args.get("from"), add_days(today_str(), -7))
        dto = parse_date(request.args.get("to"), add_days(today_str(), 60))
        q = (request.args.get("q") or "").strip().lower()
        rows = bookings_between(f"{dfrom} 00:00", f"{add_days(dto, 1)} 00:00",
                                [status] if status else None, [int(vid)] if vid.isdigit() else None)
        if q:
            rows = [b for b in rows if q in " ".join(str(b[k] or "") for k in (
                "booking_no", "event_title", "requester_name", "requester_login", "department")).lower()]
        ctx.update(rows=rows, status=status, vid=vid, dfrom=dfrom, dto=dto, q=q, all_venues=venues(False))
    elif tab == "venues":
        ctx.update(all_venues=venues(False))
    elif tab == "users":
        role = request.args.get("role", "")
        q = (request.args.get("q") or "").strip().lower()
        users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        counts = {r: sum(1 for u in users if u["role"] == r) for r in ROLE_META}
        if role:
            users = [u for u in users if u["role"] == role]
        if q:
            users = [u for u in users if q in f"{u['full_name']} {u['login_id']} {u['email'] or ''}".lower()]
        halls = {}
        for v in venues(False):
            if v["supervisor_id"]:
                halls.setdefault(v["supervisor_id"], []).append(v["code"])
        ctx.update(users=users, role=role, q=q, counts=counts, halls=halls, departments=departments())
    elif tab == "reports":
        ctx.update(report_data(*report_range()))
    else:
        abort(404)
    return render_template("admin.html", **ctx)


def report_range():
    dto = parse_date(request.args.get("to"))
    dfrom = parse_date(request.args.get("from"), add_days(dto, -29))
    return dfrom, dto


def report_data(dfrom, dto):
    rows = bookings_between(f"{dfrom} 00:00", f"{add_days(dto, 1)} 00:00")
    days = max(1, (datetime.strptime(dto, "%Y-%m-%d") - datetime.strptime(dfrom, "%Y-%m-%d")).days + 1)
    approved = [b for b in rows if b["status"] == "APPROVED"]

    def hours(b):
        return (datetime.strptime(b["end_at"], "%Y-%m-%d %H:%M")
                - datetime.strptime(b["start_at"], "%Y-%m-%d %H:%M")).total_seconds() / 3600

    per_venue = []
    for v in venues(False):
        open_h = (to_min(v["close_time"]) - to_min(v["open_time"])) / 60 * days
        used = sum(hours(b) for b in approved if b["venue_id"] == v["id"])
        per_venue.append({"name": v["name"], "code": v["code"], "hours": round(used, 1),
                          "pct": round(used / open_h * 100) if open_h else 0})
    per_venue.sort(key=lambda x: -x["hours"])
    per_dept = {}
    for b in approved:
        per_dept[b["department"] or "—"] = per_dept.get(b["department"] or "—", 0) + 1
    per_dept = sorted(per_dept.items(), key=lambda x: -x[1])
    per_status = {}
    for b in rows:
        per_status[b["status"]] = per_status.get(b["status"], 0) + 1
    decided = [b for b in rows if b["decided_at"] and b["status"] != "BLOCKED"]
    avg = 0.0
    if decided:
        avg = sum((datetime.strptime(b["decided_at"][:16], "%Y-%m-%d %H:%M")
                   - datetime.strptime(b["created_at"][:16], "%Y-%m-%d %H:%M")).total_seconds()
                  for b in decided) / len(decided) / 3600
    return {
        "dfrom": dfrom, "dto": dto, "rows": rows, "per_venue": per_venue, "per_dept": per_dept,
        "per_status": per_status, "approved_count": len(approved),
        "requests": sum(1 for b in rows if b["status"] != "BLOCKED"),
        "auto_rejected": sum(1 for b in rows if (b["decision_note"] or "").startswith("Auto-rejected")),
        "avg_hours": max(0.0, avg),
        "max_dept": max([c for _, c in per_dept] or [1]),
        "max_status": max(list(per_status.values()) or [1]),
    }


@app.route("/admin/export.xlsx")
@login_required("admin")
def admin_export():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        flash("Install openpyxl to export Excel files:  pip install openpyxl", "error")
        return redirect(url_for("admin_dashboard", tab="reports"))
    dfrom, dto = report_range()
    data = report_data(dfrom, dto)
    wb = Workbook()
    ws = wb.active
    ws.title = "Bookings"
    head = ["Booking No", "Status", "Venue", "Date", "From", "To", "Event", "Department", "Attendees",
            "Faculty in-charge", "Requested by", "Roll / Staff ID", "Role", "Decided by", "Note", "Requested at"]
    ws.append(head)
    for b in data["rows"]:
        ws.append([b["booking_no"], b["status"], b["venue_name"], b["start_at"][:10], b["start_at"][11:],
                   b["end_at"][11:], b["event_title"], b["department"], b["attendees"], b["faculty_incharge"],
                   b["requester_name"], (b["requester_login"] or "").upper(), b["requester_role"],
                   b["decided_by_name"], b["decision_note"], b["created_at"]])
    ws2 = wb.create_sheet("Venue utilisation")
    ws2.append(["Venue", "Code", "Hours booked", "Utilisation %"])
    for v in data["per_venue"]:
        ws2.append([v["name"], v["code"], v["hours"], v["pct"]])
    ws3 = wb.create_sheet("By department")
    ws3.append(["Department", "Approved bookings"])
    for d, c in data["per_dept"]:
        ws3.append([d, c])
    for sheet in wb.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1B2A5E")
        for col in sheet.columns:
            width = max(len(str(c.value or "")) for c in col) + 2
            sheet.column_dimensions[col[0].column_letter].width = min(45, width)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"MCET-venue-report-{dfrom}-to-{dto}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----- venue management -----
@app.route("/admin/venue/new", methods=["GET", "POST"])
@app.route("/admin/venue/<int:vid>", methods=["GET", "POST"])
@login_required("admin")
def admin_venue(vid=None):
    conn = get_db()
    v = conn.execute("SELECT * FROM venues WHERE id=?", (vid,)).fetchone() if vid else None
    if vid and not v:
        abort(404)
    error = None
    if request.method == "POST":
        f = request.form
        if f.get("delete") == "1" and v:
            conn.execute("DELETE FROM venues WHERE id=?", (vid,))
            flash(f"{v['name']} deleted.", "success")
            return redirect(url_for("admin_dashboard", tab="venues"))
        try:
            cap = int(f.get("capacity") or 0)
        except ValueError:
            cap = 0
        row = (f.get("code", "").strip().upper(), f.get("name", "").strip(), f.get("venue_type", "SEMINAR_HALL"),
               f.get("block", "").strip(), cap,
               ", ".join(x.strip() for x in f.get("facilities", "").split(",") if x.strip()),
               f.get("description", "").strip() or None, f.get("supervisor_id") or None,
               f.get("open_time", "08:00"), f.get("close_time", "18:00"), 1 if f.get("is_active") else 0)
        if not row[0] or not row[1] or cap < 1:
            error = "Code, name and a capacity of at least 1 are required."
        elif not (valid_time(row[8]) and valid_time(row[9])) or row[9] <= row[8]:
            error = "Closing time must be after opening time."
        else:
            try:
                if v:
                    conn.execute("UPDATE venues SET code=?, name=?, venue_type=?, block=?, capacity=?, facilities=?, "
                                 "description=?, supervisor_id=?, open_time=?, close_time=?, is_active=? WHERE id=?",
                                 (*row, vid))
                else:
                    conn.execute("INSERT INTO venues (code, name, venue_type, block, capacity, facilities, description, "
                                 "supervisor_id, open_time, close_time, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
                flash("Venue saved.", "success")
                return redirect(url_for("admin_dashboard", tab="venues"))
            except sqlite3.IntegrityError:
                error = "That venue code already exists."
    staff = conn.execute("SELECT * FROM users WHERE role IN ('supervisor','admin') ORDER BY full_name").fetchall()
    return render_template("venue_form.html", v=v, staff=staff, error=error, form=request.form if error else None)


# ----- user management -----
@app.route("/admin/users/new", methods=["POST"])
@login_required("admin")
def admin_user_new():
    f = request.form
    role = f.get("role")
    login_id = f.get("login_id", "").strip().lower()
    if role not in ("supervisor", "admin") or not login_id or len(f.get("full_name", "").strip()) < 2:
        flash("Choose a role and enter a name and staff ID.", "error")
    elif len(f.get("password", "")) < 8:
        flash("Initial password must be at least 8 characters.", "error")
    else:
        try:
            get_db().execute(
                "INSERT INTO users (role, full_name, login_id, password_hash, department, phone) VALUES (?,?,?,?,?,?)",
                (role, f["full_name"].strip(), login_id, generate_password_hash(f["password"]),
                 f.get("department") or None, f.get("phone", "").strip() or None))
            flash(f"{ROLE_META[role]['label']} account created — they sign in with staff ID “{login_id}”."
                  + (" Assign their halls from the Venues tab." if role == "supervisor" else ""), "success")
        except sqlite3.IntegrityError:
            flash(f"Staff ID “{login_id}” is already taken.", "error")
    return redirect(url_for("admin_dashboard", tab="users"))


@app.route("/admin/users/<int:uid>/<action>", methods=["POST"])
@login_required("admin")
def admin_user_action(uid, action):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    if uid == g.user["id"] and action in ("delete", "role"):
        flash("You cannot delete or change the role of your own account.", "error")
    elif action == "delete":
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        flash(f"Account of {u['full_name']} deleted.", "success")
    elif action == "reset":
        pw = request.form.get("password", "")
        if len(pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        else:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), uid))
            flash(f"Password reset for {u['full_name']}. Share it privately.", "success")
    elif action == "role":
        role = request.form.get("role")
        if role in ROLE_META:
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            if role == "student":
                conn.execute("UPDATE venues SET supervisor_id=NULL WHERE supervisor_id=?", (uid,))
            flash(f"{u['full_name']} is now {ROLE_META[role]['label']}.", "success")
    else:
        abort(404)
    return redirect(url_for("admin_dashboard", tab="users"))


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def error_page(err):
    return render_template("error.html", err=err), err.code


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    created = database.init_db()
    if created:
        print("\n  New database created with demo data. Demo logins:")
        print("    Admin       ID: admin                   password: admin@123")
        print("    Supervisor  ID: sup-cse                 password: super@123")
        print("    Student     email: student@mcet.local   password: student@123\n")
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
else:
    database.init_db()
