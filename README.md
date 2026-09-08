# Attendance Register

A school attendance website built with **Python (Flask)**, **HTML**, and **CSS**.

- Scan a student’s **face** to mark today’s attendance.
- Scan their **work copy** (keep the face in frame) to update **classwork and homework** in that student’s account.
- Students only **view** their own records.
- Super admin has full write access, adds teachers to classes, and grants **read / write / update**.

## MySQL Workbench

The app uses the MySQL connection from Workbench:

- Host: `127.0.0.1`
- Port: `3306`
- Username: `root`
- Password: `root`
- Schema: `attendance_register` (created automatically on first run)

In Workbench, open the **localhost** connection and refresh Schemas to see tables such as `users`, `classes`, `students`, `attendance`, `classwork`, and `homework`.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

MySQL Server must be running before you start the site.

## Forgot password

1. Super admin signs in, opens **Profile**, and saves each person’s **registered email**.
2. Super admin also saves **SMTP** settings on Profile (for Gmail: `smtp.gmail.com`, port `587`, app password).
3. On the login page, click **Forgot password?**, enter username or email, and check that inbox.
4. The link works **only once**. After the new password is saved, the site goes to **sign in** (not back to forgot password). Everyone must log in again.

## Demo logins

| Role | Username | Password | Notes |
| --- | --- | --- | --- |
| Super admin | `admin` | `admin123` | All writes |
| Teacher | `teacher1` | `teacher123` | Class 10-A: read, write, update |
| Teacher | `teacher2` | `teacher123` | Class 8-B: read only |
| Student | `aarav` | `student123` | Class 10-A, view only |
| Student | `meera` | `student123` | Class 8-B, view only |

Other students: `diya`, `kabir`, `vihaan` / `student123`.

## First-time face setup

1. Sign in as **admin**.
2. Open **Students**, create the account, then capture **2 to 50 different** face photos (left, right, closer, smile, straight-on).
3. Teachers open **Face & copy scan**, point the camera, and keep **Live scan** on.
4. The match fills in **class and section automatically** and saves attendance in that student’s account.

The matcher compares the live face against every enrollment photo. It works best with one student in frame and good light.
