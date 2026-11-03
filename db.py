"""
Database layer for the MCET Smart Hall & Venue Availability Management System.

Uses SQLite (built into Python) so there is nothing to install or configure.
The database file `mcet_venue.db` is created automatically on first run.

Conflict prevention is enforced INSIDE the database with triggers:
two APPROVED/BLOCKED bookings on the same venue can never overlap,
no matter which page or script tries to write them.
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import g
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).with_name("mcet_venue.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departments (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT NOT NULL CHECK (role IN ('student', 'supervisor', 'admin')),
    full_name     TEXT NOT NULL,
    login_id      TEXT NOT NULL UNIQUE,          -- roll number (students) or staff ID
    email         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    department    TEXT REFERENCES departments(code),
    phone         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS venues (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    venue_type    TEXT NOT NULL DEFAULT 'SEMINAR_HALL',
    block         TEXT NOT NULL DEFAULT '',
    capacity      INTEGER NOT NULL CHECK (capacity > 0),
    facilities    TEXT NOT NULL DEFAULT '',      -- comma separated
    description   TEXT,
    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    open_time     TEXT NOT NULL DEFAULT '08:00',
    close_time    TEXT NOT NULL DEFAULT '18:00',
    is_active     INTEGER NOT NULL DEFAULT 1,
    CHECK (close_time > open_time)
);

CREATE TABLE IF NOT EXISTS bookings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_no       TEXT UNIQUE,
    venue_id         INTEGER NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    requested_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    department       TEXT,
    event_title      TEXT NOT NULL,
    purpose          TEXT,
    attendees        INTEGER NOT NULL DEFAULT 1 CHECK (attendees > 0),
    faculty_incharge TEXT,
    start_at         TEXT NOT NULL,              -- 'YYYY-MM-DD HH:MM' (college local time)
    end_at           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED','BLOCKED')),
    decided_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decision_note    TEXT,
    decided_at       TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (end_at > start_at)
);
CREATE INDEX IF NOT EXISTS idx_bookings_venue_time ON bookings (venue_id, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings (status);

-- ===== THE conflict guarantee =====
CREATE TRIGGER IF NOT EXISTS bookings_no_overlap_insert
BEFORE INSERT ON bookings
WHEN NEW.status IN ('APPROVED', 'BLOCKED')
BEGIN
    SELECT RAISE(ABORT, 'SLOT_CONFLICT: this slot is already allotted')
    WHERE EXISTS (
        SELECT 1 FROM bookings
        WHERE venue_id = NEW.venue_id
          AND status IN ('APPROVED', 'BLOCKED')
          AND start_at < NEW.end_at AND NEW.start_at < end_at
    );
END;

CREATE TRIGGER IF NOT EXISTS bookings_no_overlap_update
BEFORE UPDATE OF status, start_at, end_at, venue_id ON bookings
WHEN NEW.status IN ('APPROVED', 'BLOCKED')
BEGIN
    SELECT RAISE(ABORT, 'SLOT_CONFLICT: this slot is already allotted')
    WHERE EXISTS (
        SELECT 1 FROM bookings
        WHERE venue_id = NEW.venue_id
          AND id <> NEW.id
          AND status IN ('APPROVED', 'BLOCKED')
          AND start_at < NEW.end_at AND NEW.start_at < end_at
    );
END;

-- Booking numbers like BK-1001
CREATE TRIGGER IF NOT EXISTS bookings_number
AFTER INSERT ON bookings
WHEN NEW.booking_no IS NULL
BEGIN
    UPDATE bookings SET booking_no = 'BK-' || (1000 + NEW.id) WHERE id = NEW.id;
END;
"""

DEPARTMENTS = [
    ("AUTO", "Automobile Engineering"),
    ("CIVIL", "Civil Engineering"),
    ("CSE", "Computer Science and Engineering"),
    ("AIDS", "Artificial Intelligence and Data Science"),
    ("ECE", "Electronics and Communication Engineering"),
    ("EEE", "Electrical and Electronics Engineering"),
    ("EIE", "Electronics and Instrumentation Engineering"),
    ("IT", "Information Technology"),
    ("MECH", "Mechanical Engineering"),
    ("MCT", "Mechatronics Engineering"),
    ("SH", "Science and Humanities"),
    ("MBA", "Master of Business Administration"),
    ("MCA", "Master of Computer Applications"),
    ("CLUBS", "Clubs, Chapters and Associations"),
    ("ADMIN", "Administration / Office"),
]

# Sample venues — edit these from Admin → Venues to match the real halls.
VENUES = [
    ("AUD-MAIN", "Main Auditorium", "AUDITORIUM", "Main Block", 1200, "Projector, Sound system, Stage, AC, Green room", "08:00", "20:00"),
    ("CONF-01", "Conference Hall", "CONFERENCE_ROOM", "Admin Block", 60, "Projector, AC, Video conferencing, Podium", "08:00", "18:00"),
    ("SEM-CSE", "CSE Seminar Hall", "SEMINAR_HALL", "CSE Block", 180, "Projector, Sound system, AC", "08:00", "18:00"),
    ("SEM-ECE", "ECE Seminar Hall", "SEMINAR_HALL", "ECE Block", 150, "Projector, Sound system, AC", "08:00", "18:00"),
    ("SEM-MECH", "Mechanical Seminar Hall", "SEMINAR_HALL", "Mechanical Block", 150, "Projector, Sound system", "08:00", "18:00"),
    ("SEM-LIB", "Library Seminar Hall", "SEMINAR_HALL", "Library Block", 120, "Projector, AC", "08:00", "18:00"),
    ("LAB-IT1", "IT Lab 1 (Hackathon Lab)", "LAB", "IT Block", 70, "70 systems, Projector, Wi-Fi", "08:00", "18:00"),
    ("OAT", "Open Air Theatre", "OUTDOOR", "Campus Centre", 800, "Stage, Sound system", "07:00", "21:00"),
    ("IND-STAD", "Indoor Stadium", "SPORTS", "Sports Complex", 500, "Courts, Sound system", "06:00", "20:00"),
    ("BOARD", "Board Room", "CONFERENCE_ROOM", "Admin Block", 25, "Smart TV, AC, Video conferencing", "08:00", "18:00"),
]

# Demo accounts created on first run (change the passwords after signing in!)
DEMO_USERS = [
    # role, full name, login_id, email, password, dept
    ("admin", "Admin Office", "admin", None, "admin@123", "ADMIN"),
    ("supervisor", "Mr. K. Senthil", "sup-cse", None, "super@123", "CSE"),
    ("supervisor", "Ms. P. Divya", "sup-aud", None, "super@123", "EEE"),
    ("student", "Demo Student", "727622bcs001", "student@mcet.local", "student@123", "CSE"),
    ("student", "Arjun S", "727622bec012", "arjun@mcet.local", "student@123", "ECE"),
]


def get_db():
    """One SQLite connection per request."""
    if "db" not in g:
        g.db = connect()
    return g.db


def connect():
    conn = sqlite3.connect(DB_PATH, detect_types=0, isolation_level=None)  # autocommit; we BEGIN explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(with_demo_data=True):
    """Create tables and, on a brand-new database, seed departments, venues, demo users and bookings."""
    fresh = not DB_PATH.exists()
    conn = connect()
    conn.executescript(SCHEMA)
    if fresh:
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO departments (code, name, sort_order) VALUES (?, ?, ?)",
            [(c, n, i) for i, (c, n) in enumerate(DEPARTMENTS)],
        )
        conn.executemany(
            "INSERT INTO venues (code, name, venue_type, block, capacity, facilities, open_time, close_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            VENUES,
        )
        ids = {}
        for role, name, login_id, email, pw, dept in DEMO_USERS:
            cur = conn.execute(
                "INSERT INTO users (role, full_name, login_id, email, password_hash, department) VALUES (?,?,?,?,?,?)",
                (role, name, login_id, email, generate_password_hash(pw), dept),
            )
            ids[login_id] = cur.lastrowid
        # assign supervisors to halls
        conn.execute("UPDATE venues SET supervisor_id=? WHERE code IN ('SEM-CSE','LAB-IT1')", (ids["sup-cse"],))
        conn.execute("UPDATE venues SET supervisor_id=? WHERE code IN ('AUD-MAIN','OAT')", (ids["sup-aud"],))
        if with_demo_data:
            _seed_bookings(conn, ids)
        conn.execute("COMMIT")
    conn.close()
    return fresh


def _seed_bookings(conn, ids):
    venue = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM venues")}
    today = datetime.now().date()

    def at(day, hhmm):
        return f"{today + timedelta(days=day):%Y-%m-%d} {hhmm}"

    rows = [
        # venue, title, day, start, end, status, requester, dept, attendees
        ("AUD-MAIN", "Annual Day Rehearsal", 0, "09:00", "12:30", "APPROVED", "sup-aud", "CLUBS", 400),
        ("AUD-MAIN", "Placement Orientation", 1, "14:00", "16:00", "PENDING", "727622bec012", "ECE", 900),
        ("SEM-CSE", "IEEE Guest Lecture: GenAI", 0, "10:00", "12:00", "APPROVED", "727622bcs001", "CSE", 150),
        ("SEM-CSE", "Coding Club Meetup", 1, "13:00", "14:30", "PENDING", "727622bcs001", "CSE", 60),
        ("SEM-CSE", "Robotics Workshop", 1, "13:30", "15:30", "PENDING", "727622bec012", "ECE", 90),
        ("SEM-CSE", "Projector repair", 1, "16:00", "17:00", "BLOCKED", "sup-cse", "CSE", 1),
        ("SEM-ECE", "ECE Symposium", 0, "09:30", "13:00", "APPROVED", "727622bec012", "ECE", 140),
        ("SEM-MECH", "FDP on CAD", 1, "11:00", "17:00", "APPROVED", "sup-cse", "MECH", 50),
        ("LAB-IT1", "Hackathon Round 1", 1, "08:30", "11:30", "APPROVED", "727622bcs001", "IT", 68),
        ("CONF-01", "IQAC meeting", 0, "15:00", "16:30", "APPROVED", "admin", "ADMIN", 30),
        ("OAT", "Music club practice", 2, "17:00", "19:00", "PENDING", "727622bec012", "CLUBS", 40),
    ]
    for code, title, day, s, e, status, who, dept, att in rows:
        decided = None if status == "PENDING" else ids["admin"]
        conn.execute(
            "INSERT INTO bookings (venue_id, requested_by, department, event_title, attendees, faculty_incharge, "
            "start_at, end_at, status, decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (venue[code], ids[who], dept, title, att, "Dr. S. Kumar", at(day, s), at(day, e), status, decided,
             None if decided is None else datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
