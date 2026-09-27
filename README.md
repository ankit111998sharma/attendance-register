# Attendance Register

A school attendance website for classes, teachers, and students. Staff mark presence with a camera, attach classwork and homework from a work-copy scan, and students only see their own records.

Copyright © Ankit Sharma.

## About this project

Attendance Register is a local Flask site backed by MySQL. Super admin owns the school, adds teachers to classes, and sets read / write / update rights. Teachers scan a student’s face to mark today’s attendance. They can also scan a work copy (with the face still in frame) to store classwork and homework on that student’s account. Students sign in only to view their own attendance and work.

## Purpose

- Keep daily attendance in one place instead of paper registers.
- Tie classwork and homework to the correct student without typing names each time.
- Give teachers limited access per class, and students a view-only account.

## How it works

1. MySQL Server must be running. On first start the app creates the `attendance_register` schema and tables (`users`, `classes`, `students`, `attendance`, `classwork`, `homework`, and related tables).
2. Super admin creates classes and student accounts, then captures **2 to 50** face photos per student (different poses and distances). Encodings are stored locally under `instance/encodings`.
3. Teachers open **Face & copy scan**, keep **Live scan** on, and point the camera at one student. The matcher compares the live face to enrollment photos, fills class and section, and saves attendance.
4. For work copies, the teacher keeps the student’s face in frame so the same match can attach scans to that account.
5. Forgot-password uses SMTP settings saved on the super admin Profile page. Reset links work once, then everyone signs in again with the new password.

Default MySQL (Workbench): host `127.0.0.1`, port `3306`, user `root`, password `root`, schema `attendance_register`.

## Advantages

- Face match reduces wrong-student attendance when lighting is good and one face is in frame.
- Role-based access: students cannot edit records; teachers only get the rights the admin grants.
- Excel import/export and PDF/Word-style reports for office use.
- Runs on a school PC with a webcam; no cloud account is required for core attendance.

## Technologies

| Area | Choice |
| --- | --- |
| Language | Python |
| Web | Flask, Flask-SQLAlchemy, Jinja HTML templates, CSS |
| Database | MySQL via PyMySQL |
| Vision | OpenCV, NumPy, Pillow |
| Office files | openpyxl, reportlab, python-docx |
| Mail | SMTP (for example Gmail on port 587 with an app password) |

## How to run this project

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Start MySQL Server first.

### Demo logins

| Role | Username | Password | Notes |
| --- | --- | --- | --- |
| Super admin | `admin` | `admin123` | All writes |
| Teacher | `teacher1` | `teacher123` | Class 10-A: read, write, update |
| Teacher | `teacher2` | `teacher123` | Class 8-B: read only |
| Student | `aarav` | `student123` | Class 10-A, view only |
| Student | `meera` | `student123` | Class 8-B, view only |

Other students: `diya`, `kabir`, `vihaan` / `student123`.

### First-time face setup

1. Sign in as **admin**.
2. Open **Students**, create the account, then capture 2 to 50 different face photos.
3. Teachers open **Face & copy scan**, point the camera, and keep **Live scan** on.

Copyright © Ankit Sharma.
