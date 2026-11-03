# MCET Smart Hall & Venue Availability Management System (Python / Flask)

Dr. Mahalingam College of Engineering and Technology, Pollachi.

A centralised website to **view hall and venue availability, manage reservations, and avoid scheduling
conflicts** when several departments need the same facilities. It has three logins: **Student**, **Hall Supervisor**
and **Admin**.

Written in **Python (Flask)** with a **SQLite** database, which is built into Python. You don't need a database
server, Node.js or any cloud account.

---

## Run it in VS Code (about 2 minutes)

1. Install **Python 3.10 or newer** from python.org. On Windows, tick *"Add Python to PATH"* during setup.
2. In VS Code: **File → Open Folder…** and choose this `mcet_venue_python` folder.
3. Open a terminal with **Terminal → New Terminal** and run:

   ```bash
   # (optional but recommended) create a virtual environment
   python -m venv .venv
   # Windows:      .venv\Scripts\activate
   # macOS/Linux:  source .venv/bin/activate

   pip install -r requirements.txt
   python app.py
   ```

4. Open **http://127.0.0.1:5000** in your browser.

You can also press **F5** in VS Code and choose *"Run MCET Venue System"*. This needs the Python extension.

On the first run the app creates `mcet_venue.db` with sample venues, sample bookings and these demo accounts:

| Login | ID | Password |
|---|---|---|
| Admin | `admin` | `admin@123` |
| Hall Supervisor | `sup-cse` (CSE Seminar Hall, IT Lab 1) | `super@123` |
| Hall Supervisor | `sup-aud` (Main Auditorium, Open Air Theatre) | `super@123` |
| Student | `student@mcet.local` | `student@123` |

Change these passwords (🔑 in the top bar) before real use. To start fresh, stop the app, delete
`mcet_venue.db` and run it again.

---

## What each role can do

**Student**
- Register with name, roll number, department and email
- See the live **availability grid** (venues × hours) for any date
- Click a free slot to request it. The form checks for clashes as you type and suggests other free halls that fit
- Track requests (upcoming / pending / history) and cancel their own bookings

**Hall Supervisor** (account created by the admin)
- See pending requests for **their** halls and approve or reject them with a note
- Get a warning when two requests compete for the same slot
- Block slots for maintenance or reserve them, and book directly

**Admin**
- Get an overview with alerts (competing requests, requests starting soon, halls without a supervisor) and today's
  grid for the whole campus
- Search all requests and approve, reject or cancel any of them
- Add, edit and deactivate venues and assign a supervisor to each
- Add supervisor or admin accounts, reset passwords and change roles
- See reports: utilisation per venue, bookings per department and outcomes, with **Excel export**

## How conflicts are avoided

- **Database triggers** (`db.py`) make it impossible for two *approved/blocked* bookings on the same venue to
  overlap. The rule holds even if someone edits the database directly.
- Requests are refused if the slot is taken, the venue is too small, the time is outside the venue's hours, the time
  has passed, or the same person already has a pending request for that slot.
- When a supervisor approves one request, every overlapping pending request is **auto-rejected** with a note that
  says which booking got the slot.
- Only the supervisor assigned to a venue, or an admin, can approve its requests. Every form is CSRF-protected,
  and passwords are stored hashed.

## Project structure

```
mcet_venue_python/
├── app.py              ← all pages, booking rules, permissions (start here)
├── db.py               ← database schema, conflict triggers, sample data
├── requirements.txt    ← Flask, openpyxl
├── templates/          ← HTML pages (Jinja2)
│   ├── base.html  _macros.html  home.html  login.html  register.html
│   ├── availability.html  request.html  booking.html
│   ├── student.html  supervisor.html  admin.html  venue_form.html
└── static/
    ├── style.css       ← theme (navy & gold, dark mode)
    ├── app.js          ← grid click-to-book + live availability check
    └── mcet-logo.svg   ← logo (replace with the official MCET logo if you like)
```

**Customising:** edit the sample venues and departments in `db.py` (or from Admin → Venues once it is running). To
use the official MCET logo, replace `static/mcet-logo.svg` and `static/favicon.png`.
