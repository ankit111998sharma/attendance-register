from datetime import date, datetime, timedelta
from functools import wraps
from hashlib import sha256
from pathlib import Path
import secrets
import shutil
from urllib.parse import quote_plus

import pymysql
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, or_, text

from excel_import import (
    account_conflict,
    credentials_workbook,
    excel_template,
    parse_student_rows,
    parse_teacher_rows,
)
from exports import build_student_export, build_teacher_export, school_title
from face_engine import (
    MAX_FACE_PHOTOS,
    MIN_FACE_PHOTOS,
    extract_encoding,
    is_too_similar,
    load_encoding,
    match_student,
    pose_prompt,
    save_encoding,
    save_preview,
)
from mail_utils import get_setting, mail_configured, mail_settings, send_reset_email, set_setting
from models import (
    Attendance,
    Classwork,
    Homework,
    PasswordResetToken,
    SchoolClass,
    Student,
    Teacher,
    TeacherPermission,
    User,
    db,
    IST,
    naive_ist,
    now_ist,
    today_ist,
)

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
FACE_DIR = UPLOAD_DIR / "faces"
WORK_DIR = UPLOAD_DIR / "work"
ENC_DIR = INSTANCE_DIR / "encodings"

MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = "root"
MYSQL_DB = "attendance_register"


def mysql_uri():
    password = quote_plus(MYSQL_PASSWORD)
    return (
        f"mysql+pymysql://{MYSQL_USER}:{password}@{MYSQL_HOST}:{MYSQL_PORT}/"
        f"{MYSQL_DB}?charset=utf8mb4"
    )


def ensure_mysql_database():
    connection = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()


def _column_names(table_name):
    with db.engine.begin() as connection:
        return {row[0] for row in connection.execute(text(f"SHOW COLUMNS FROM {table_name}"))}


def _add_column_if_missing(table_name, column_name, ddl):
    if column_name in _column_names(table_name):
        return
    with db.engine.begin() as connection:
        connection.execute(text(ddl))


def _index_names(table_name):
    with db.engine.begin() as connection:
        return {row[2] for row in connection.execute(text(f"SHOW INDEX FROM {table_name}"))}


def _drop_index_if_exists(table_name, index_name):
    if index_name not in _index_names(table_name):
        return
    with db.engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} DROP INDEX `{index_name}`"))


def _add_index_if_missing(table_name, index_name, ddl):
    if index_name in _index_names(table_name):
        return
    with db.engine.begin() as connection:
        connection.execute(text(ddl))


def ensure_schema_columns():
    _add_column_if_missing(
        "students",
        "face_photo_count",
        "ALTER TABLE students ADD COLUMN face_photo_count INT NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "users",
        "email",
        "ALTER TABLE users ADD COLUMN email VARCHAR(120) NULL",
    )
    _add_column_if_missing(
        "users",
        "is_active",
        "ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(
        "users",
        "must_change_password",
        "ALTER TABLE users ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "users",
        "temp_password",
        "ALTER TABLE users ADD COLUMN temp_password VARCHAR(80) NULL",
    )
    _add_column_if_missing(
        "users",
        "created_at",
        "ALTER TABLE users ADD COLUMN created_at DATETIME NULL",
    )
    _add_column_if_missing(
        "teacher_permissions",
        "can_deactivate",
        "ALTER TABLE teacher_permissions ADD COLUMN can_deactivate TINYINT(1) NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "students",
        "admission_number",
        "ALTER TABLE students ADD COLUMN admission_number VARCHAR(40) NULL",
    )
    _add_column_if_missing(
        "teachers",
        "employee_id",
        "ALTER TABLE teachers ADD COLUMN employee_id VARCHAR(40) NULL",
    )
    _add_column_if_missing(
        "teachers",
        "can_reports",
        "ALTER TABLE teachers ADD COLUMN can_reports TINYINT(1) NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "attendance",
        "period",
        "ALTER TABLE attendance ADD COLUMN period VARCHAR(20) NOT NULL DEFAULT '1'",
    )
    _add_column_if_missing(
        "attendance",
        "class_id",
        "ALTER TABLE attendance ADD COLUMN class_id INT NULL",
    )
    _add_column_if_missing(
        "attendance",
        "marked_at",
        "ALTER TABLE attendance ADD COLUMN marked_at DATETIME NULL",
    )
    _add_column_if_missing(
        "attendance",
        "subject",
        "ALTER TABLE attendance ADD COLUMN subject VARCHAR(80) NOT NULL DEFAULT ''",
    )
    with db.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE attendance a
                JOIN students s ON s.id = a.student_id
                SET a.class_id = s.class_id
                WHERE a.class_id IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE attendance
                SET marked_at = COALESCE(marked_at, created_at, NOW())
                WHERE marked_at IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE attendance a
                LEFT JOIN classes c ON c.id = a.class_id
                SET a.subject = LEFT(
                    COALESCE(
                        NULLIF(TRIM(c.name), ''),
                        CONCAT(c.grade, '-', c.section),
                        NULLIF(TRIM(a.subject), ''),
                        'Class'
                    ),
                    80
                )
                WHERE a.subject IS NULL
                   OR a.subject = ''
                   OR a.subject IN ('1','2','3','4','5','6','7','8')
                """
            )
        )
    # The old unique key also served the student_id foreign key, so add a
    # dedicated index before dropping it.
    _add_index_if_missing(
        "attendance",
        "idx_attendance_student_id",
        "ALTER TABLE attendance ADD INDEX idx_attendance_student_id (student_id)",
    )
    _add_index_if_missing(
        "attendance",
        "idx_attendance_class_id",
        "ALTER TABLE attendance ADD INDEX idx_attendance_class_id (class_id)",
    )
    _drop_index_if_exists("attendance", "uq_student_day")
    _drop_index_if_exists("attendance", "uq_student_class_day_period")
    _add_index_if_missing(
        "attendance",
        "idx_attendance_student_subject_day",
        "ALTER TABLE attendance ADD INDEX idx_attendance_student_subject_day "
        "(student_id, subject, day)",
    )


def create_app():
    app = Flask(__name__)
    INSTANCE_DIR.mkdir(exist_ok=True)
    FACE_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    ENC_DIR.mkdir(parents=True, exist_ok=True)
    ensure_mysql_database()

    app.config["SECRET_KEY"] = "attendance-register-demo-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = mysql_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_schema_columns()
        seed_if_empty()
        backfill_missing_emails()
        backfill_identity_numbers()
        backfill_face_enrolled()

    register_routes(app)
    return app


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user and user.is_active is False:
            session.clear()
            flash("Your account has been deactivated. Ask the super admin to restore access.", "error")
            return redirect(url_for("login"))
        if not user:
            flash("Please sign in to continue.", "error")
            return redirect(url_for("login"))
        if user.must_change_password and request.endpoint != "change_own_password":
            flash("Set a new password before you continue. The first password was temporary.", "error")
            return redirect(url_for("change_own_password"))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user and user.is_active is False:
                session.clear()
                flash("Your account has been deactivated. Ask the super admin to restore access.", "error")
                return redirect(url_for("login"))
            if not user:
                return redirect(url_for("login"))
            if user.role not in roles:
                flash("You do not have access to that page.", "error")
                return redirect(url_for("dashboard"))
            if user.must_change_password and request.endpoint != "change_own_password":
                flash("Set a new password before you continue. The first password was temporary.", "error")
                return redirect(url_for("change_own_password"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def teacher_permission(user, class_id):
    if not user:
        return None
    if user.is_admin:
        return TeacherPermission(
            can_read=True, can_write=True, can_update=True, can_deactivate=True
        )
    if not user.is_teacher or not user.teacher_profile:
        return None
    return TeacherPermission.query.filter_by(
        teacher_id=user.teacher_profile.id, class_id=class_id
    ).first()


def can(user, class_id, action):
    perm = teacher_permission(user, class_id)
    if not perm:
        return False
    return bool(getattr(perm, f"can_{action}", False))


def can_any(user, class_id, *actions):
    if not user or not class_id:
        return False
    if user.is_admin:
        return True
    return any(can(user, class_id, action) for action in actions)


def can_take_register(user, class_id):
    return can_any(user, class_id, "write", "update")


def register_class_ids(user):
    if not user:
        return []
    if user.is_admin:
        return [item.id for item in SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()]
    return [item.id for item in visible_classes(user) if can_take_register(user, item.id)]


def apply_class_permission(teacher_id, class_id, can_read, can_write, can_update, can_deactivate=False):
    perm = TeacherPermission.query.filter_by(teacher_id=teacher_id, class_id=class_id).first()
    if perm is None:
        perm = TeacherPermission(teacher_id=teacher_id, class_id=class_id)
        db.session.add(perm)
    perm.can_write = bool(can_write)
    perm.can_update = bool(can_update)
    perm.can_deactivate = bool(can_deactivate)
    perm.can_read = (
        bool(can_read) or perm.can_write or perm.can_update or perm.can_deactivate
    )
    return perm


def can_deactivate_user(actor, target):
    if not actor or not target:
        return False
    if target.id == actor.id:
        return False
    if target.is_admin:
        return False
    if actor.is_admin:
        return target.is_student or target.is_teacher
    if actor.is_teacher and target.is_student and target.student_profile:
        return can(actor, target.student_profile.class_id, "deactivate")
    return False


def can_manage_student(user, student):
    if not user or not student:
        return False
    if user.is_admin:
        return True
    return can(user, student.class_id, "update")


def safe_next(value, fallback):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def purge_student_files(student_id):
    for path in (FACE_DIR / f"{student_id}.jpg", ENC_DIR / f"{student_id}.npy"):
        if path.exists():
            path.unlink()
    for folder in (photo_dir(student_id), encoding_dir(student_id)):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)


def remove_student_record(student):
    name = student.user.full_name if student.user else "Student"
    account = student.user
    Attendance.query.filter_by(student_id=student.id).delete()
    Classwork.query.filter_by(student_id=student.id).delete()
    Homework.query.filter_by(student_id=student.id).delete()
    if account:
        PasswordResetToken.query.filter_by(user_id=account.id).delete()
    purge_student_files(student.id)
    db.session.delete(student)
    if account:
        db.session.delete(account)
    return name


def student_ids_from_form():
    ids = []
    for raw in request.form.getlist("student_id"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    return list(dict.fromkeys(ids))


def class_ids_from_form():
    ids = []
    for raw in request.form.getlist("class_id"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    existing = {
        item.id for item in SchoolClass.query.filter(SchoolClass.id.in_(ids)).all()
    }
    return [value for value in ids if value in existing]


def apply_class_permissions(teacher_id, class_ids, can_read, can_write, can_update, can_deactivate=False):
    for class_id in class_ids:
        apply_class_permission(
            teacher_id, class_id, can_read, can_write, can_update, can_deactivate
        )
    return len(class_ids)


def visible_classes(user):
    if user.is_admin:
        return SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()
    if user.is_teacher and user.teacher_profile:
        ids = [
            p.class_id
            for p in user.teacher_profile.permissions
            if p.can_read or p.can_write or p.can_update or p.can_deactivate
        ]
        if not ids:
            return []
        return SchoolClass.query.filter(SchoolClass.id.in_(ids)).order_by(SchoolClass.grade).all()
    return []


def encoding_dir(student_id):
    return ENC_DIR / str(student_id)


def photo_dir(student_id):
    return FACE_DIR / str(student_id)


def migrate_legacy_encoding(student_id):
    legacy = ENC_DIR / f"{student_id}.npy"
    folder = encoding_dir(student_id)
    if not legacy.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "000.npy"
    if dest.exists():
        legacy.unlink()
    else:
        legacy.replace(dest)


def list_encoding_paths(student_id):
    migrate_legacy_encoding(student_id)
    folder = encoding_dir(student_id)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.npy"))


def enrolled_faces(class_ids=None):
    gallery = []
    query = Student.query.join(User)
    if class_ids is not None:
        if not class_ids:
            return []
        query = query.filter(Student.class_id.in_(class_ids))
    for student in query.all():
        if student.user and student.user.is_active is False:
            continue
        paths = [str(path) for path in list_encoding_paths(student.id)]
        if paths:
            gallery.append((student.id, paths))
    return gallery


def load_student_encodings(student_id):
    encodings = []
    for path in list_encoding_paths(student_id):
        item = load_encoding(path)
        if item is not None:
            encodings.append(item)
    return encodings


def store_face_sample(student, file_bytes):
    encoding = extract_encoding(file_bytes)
    if encoding is None:
        return {"ok": False, "error": "no_face"}

    existing = load_student_encodings(student.id)
    if len(existing) >= MAX_FACE_PHOTOS:
        return {"ok": False, "error": "max", "count": len(existing)}
    if existing and is_too_similar(encoding, existing):
        return {
            "ok": False,
            "error": "similar",
            "count": len(existing),
            "prompt": pose_prompt(len(existing)),
        }

    index = len(existing)
    save_encoding(encoding_dir(student.id) / f"{index:03d}.npy", encoding)
    sample_path = photo_dir(student.id) / f"{index:03d}.jpg"
    save_preview(sample_path, file_bytes)
    if index == 0:
        preview = FACE_DIR / f"{student.id}.jpg"
        save_preview(preview, file_bytes)
        student.face_image_path = f"uploads/faces/{student.id}.jpg"

    count = index + 1
    student.face_photo_count = count
    student.face_enrolled = count >= MIN_FACE_PHOTOS
    db.session.commit()
    return {
        "ok": True,
        "count": count,
        "min": MIN_FACE_PHOTOS,
        "max": MAX_FACE_PHOTOS,
        "ready": student.face_enrolled,
        "prompt": pose_prompt(count),
        "photo": f"uploads/faces/{student.id}/{index:03d}.jpg",
    }


CLASS_SESSION_MINUTES = 60
OLD_PERIOD_VALUES = {str(number) for number in range(1, 9)}


def class_subject(school_class, override=None):
    text = str(override or "").strip()
    if text and text not in OLD_PERIOD_VALUES:
        return text[:80]
    if not school_class:
        return text[:80] if text else "Class"
    name = (school_class.name or "").strip()
    if name:
        return name[:80]
    grade = (school_class.grade or "").strip()
    section = (school_class.section or "").strip()
    label = "-".join(part for part in (grade, section) if part)
    return (label or "Class")[:80]


def subject_choices(classes):
    names = []
    seen = set()
    for item in classes or []:
        name = class_subject(item)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def attendance_subject(record):
    if not record:
        return "Class"
    school_class = record.school_class
    if school_class is None and record.student:
        school_class = record.student.school_class
    return class_subject(school_class, getattr(record, "subject", None) or record.period)


def attendance_time(record):
    stamp = record.marked_at or record.created_at
    if not stamp:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    else:
        stamp = stamp.astimezone(IST)
    return stamp.strftime("%I:%M %p").lstrip("0") + " IST"


def session_cutoff(when=None):
    return naive_ist(when) - timedelta(minutes=CLASS_SESSION_MINUTES)


def recent_subject_record(student, subject, when=None):
    when = when or now_ist()
    return (
        Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.subject == subject,
            Attendance.day == today_ist(when),
            Attendance.marked_at >= session_cutoff(when),
        )
        .order_by(Attendance.marked_at.asc(), Attendance.id.asc())
        .first()
    )


def lesson_class_for_user(user, class_id):
    if not class_id:
        return None
    school_class = db.session.get(SchoolClass, class_id)
    if not school_class:
        return None
    if user and (user.is_admin or can_take_register(user, school_class.id)):
        return school_class
    return None


def mark_attendance(student, user, source="face_scan", subject=None, status="present", school_class=None):
    if status not in {"present", "absent", "late"}:
        status = "present"
    school_class = school_class or student.school_class
    subject = class_subject(school_class, subject)
    now = now_ist()
    record = recent_subject_record(student, subject, now)
    if record:
        return record, False
    record = Attendance(
        student_id=student.id,
        class_id=student.class_id,
        day=today_ist(now),
        period="1",
        subject=subject,
        status=status,
        marked_by_id=user.id if user else None,
        source=source,
        marked_at=naive_ist(now),
    )
    db.session.add(record)
    db.session.commit()
    return record, True


def mark_work(student, user, image_path=None):
    today = today_ist()
    title_stamp = today.strftime("%d %b %Y")
    cw = Classwork.query.filter_by(student_id=student.id, day=today).first()
    if cw is None:
        cw = Classwork(
            student_id=student.id,
            day=today,
            title=f"Classwork · {title_stamp}",
            status="submitted",
            image_path=image_path,
            marked_by_id=user.id,
        )
        db.session.add(cw)
    else:
        cw.status = "submitted"
        cw.marked_by_id = user.id
        if image_path:
            cw.image_path = image_path

    hw = Homework.query.filter_by(student_id=student.id, day=today).first()
    if hw is None:
        hw = Homework(
            student_id=student.id,
            day=today,
            title=f"Homework · {title_stamp}",
            status="submitted",
            image_path=image_path,
            marked_by_id=user.id,
        )
        db.session.add(hw)
    else:
        hw.status = "submitted"
        hw.marked_by_id = user.id
        if image_path:
            hw.image_path = image_path
    db.session.commit()
    return cw, hw


def hash_reset_token(token):
    return sha256(token.encode("utf-8")).hexdigest()


def normalize_email(value):
    return (value or "").strip().lower()


def email_taken(email, exclude_user_id=None):
    if not email:
        return False
    query = User.query.filter(User.email == email)
    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)
    return query.first() is not None


def unused_reset_row(raw_token):
    if not raw_token:
        return None
    return PasswordResetToken.query.filter_by(
        token_hash=hash_reset_token(raw_token), used=False
    ).first()


def issue_reset_token(user):
    PasswordResetToken.query.filter_by(user_id=user.id, used=False).update(
        {"used": True}, synchronize_session=False
    )
    raw = secrets.token_urlsafe(32)
    db.session.add(
        PasswordResetToken(user_id=user.id, token_hash=hash_reset_token(raw), used=False)
    )
    db.session.commit()
    return raw


def parse_filter_date(value):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def registration_date_range(values):
    start = parse_filter_date(values.get("registered_from"))
    end = parse_filter_date(values.get("registered_to"))
    if start and not end:
        end = start
    if end and not start:
        start = end
    if start and end and start > end:
        start, end = end, start
    return start, end


def apply_registration_filter(query, start, end):
    if start and end:
        return query.filter(
            func.date(User.created_at) >= start,
            func.date(User.created_at) <= end,
        )
    return query


def date_filter_label(start, end):
    if not start or not end:
        return ""
    if start == end:
        return f"registered on {start.strftime('%d %b %Y')}"
    return f"registered from {start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"


def issued_password_status(user):
    if user.temp_password:
        return "stored"
    if user.must_change_password:
        return "missing"
    return "changed"


def issued_password_rows(records, kind):
    rows = []
    for record in records:
        user = record.user
        if kind == "students":
            detail = record.admission_number or record.user.username
        else:
            detail = record.employee_id or record.subject or "—"
        rows.append(
            {
                "name": user.full_name,
                "username": user.username,
                "password": user.temp_password or "",
                "status": issued_password_status(user),
                "detail": detail,
                "registered": user.created_at.strftime("%Y-%m-%d") if user.created_at else "",
            }
        )
    return rows


def filter_student_query(args):
    rows = Student.query.join(User).join(SchoolClass)
    class_id = args.get("class_id", type=int)
    grade = (args.get("grade") or "").strip()
    section = (args.get("section") or "").strip()
    query_text = (args.get("q") or "").strip()
    start, end = registration_date_range(args)
    if class_id:
        rows = rows.filter(Student.class_id == class_id)
    else:
        if grade:
            rows = rows.filter(SchoolClass.grade == grade)
        if section:
            rows = rows.filter(SchoolClass.section == section)
    if query_text:
        like = f"%{query_text}%"
        rows = rows.filter(
            or_(
                User.full_name.ilike(like),
                User.username.ilike(like),
                Student.admission_number.ilike(like),
                Student.roll_number.ilike(like),
            )
        )
    access = (args.get("access") or "").strip()
    if access == "active":
        rows = rows.filter(User.is_active.is_(True))
    elif access == "deactivated":
        rows = rows.filter(User.is_active.is_(False))
    face = (args.get("face") or "").strip()
    if face == "ready":
        rows = rows.filter(Student.face_enrolled.is_(True))
    elif face == "pending":
        rows = rows.filter(Student.face_enrolled.is_(False))
    rows = apply_registration_filter(rows, start, end)
    filters = {
        "class_id": class_id,
        "grade": grade,
        "section": section,
        "search": query_text,
        "access": access,
        "face": face,
        "registered_from": (args.get("registered_from") or "").strip(),
        "registered_to": (args.get("registered_to") or "").strip(),
        "date_filter": bool(start),
        "date_label": date_filter_label(start, end),
    }
    return rows, filters


def can_access_reports(user):
    if not user:
        return False
    if user.is_admin:
        return True
    return bool(user.is_teacher and user.teacher_profile and user.teacher_profile.can_reports)


def can_export_students(user, args):
    if can_access_reports(user):
        return True
    class_id = args.get("class_id", type=int)
    return bool(user and user.is_teacher and class_id and can(user, class_id, "read"))


def exportable_students(user, args):
    query, filters = filter_student_query(args)
    if user.is_teacher and not can_access_reports(user):
        allowed = [item.id for item in visible_classes(user)]
        if not allowed:
            return [], filters
        query = query.filter(Student.class_id.in_(allowed))
    students = query.order_by(SchoolClass.grade, SchoolClass.section, Student.roll_number).all()
    if filters.get("class_id"):
        school_class = db.session.get(SchoolClass, filters["class_id"])
        if school_class:
            filters["class_label"] = f"{school_class.grade}-{school_class.section}"
    return students, filters


def filter_teacher_query(args):
    rows = Teacher.query.join(User)
    query_text = (args.get("q") or "").strip()
    start, end = registration_date_range(args)
    if query_text:
        like = f"%{query_text}%"
        rows = rows.filter(
            or_(
                User.full_name.ilike(like),
                User.username.ilike(like),
                Teacher.employee_id.ilike(like),
                Teacher.subject.ilike(like),
            )
        )
    grade = (args.get("grade") or "").strip()
    section = (args.get("section") or "").strip()
    if grade or section:
        rows = rows.join(TeacherPermission).join(SchoolClass)
        if grade:
            rows = rows.filter(SchoolClass.grade == grade)
        if section:
            rows = rows.filter(SchoolClass.section == section)
        rows = rows.distinct()
    access = (args.get("access") or "").strip()
    if access == "active":
        rows = rows.filter(User.is_active.is_(True))
    elif access == "deactivated":
        rows = rows.filter(User.is_active.is_(False))
    reports = (args.get("reports") or "").strip()
    if reports == "yes":
        rows = rows.filter(Teacher.can_reports.is_(True))
    elif reports == "no":
        rows = rows.filter(Teacher.can_reports.is_(False))
    rows = apply_registration_filter(rows, start, end)
    filters = {
        "search": query_text,
        "grade": grade,
        "section": section,
        "access": access,
        "reports": reports,
        "registered_from": (args.get("registered_from") or "").strip(),
        "registered_to": (args.get("registered_to") or "").strip(),
        "date_filter": bool(start),
        "date_label": date_filter_label(start, end),
    }
    return rows, filters


def invalidate_reset_tokens(user_id):
    PasswordResetToken.query.filter_by(user_id=user_id, used=False).update(
        {"used": True}, synchronize_session=False
    )


def day_range(days):
    today = today_ist()
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def count_attendance(day, statuses=None, class_ids=None):
    query = Attendance.query.filter(Attendance.day == day)
    if statuses:
        query = query.filter(Attendance.status.in_(statuses))
    if class_ids is not None:
        if not class_ids:
            return 0
        query = query.filter(Attendance.class_id.in_(class_ids))
    return query.with_entities(Attendance.student_id).distinct().count()


def student_chart_data(student, stats):
    days = day_range(14)
    priority = {"present": 0, "late": 1, "absent": 2}
    rows = {}
    for item in Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.day >= days[0],
    ).all():
        previous = rows.get(item.day)
        if previous is None or priority.get(item.status, 9) < priority.get(previous, 9):
            rows[item.day] = item.status
    return {
        "role": "student",
        "mix": {
            "present": stats["present"],
            "late": stats["late"],
            "absent": stats["absent"],
        },
        "trend": {
            "labels": [day.strftime("%d %b") for day in days],
            "present": [1 if rows.get(day) == "present" else 0 for day in days],
            "late": [1 if rows.get(day) == "late" else 0 for day in days],
            "absent": [1 if rows.get(day) == "absent" else 0 for day in days],
        },
        "work": {
            "classwork": Classwork.query.filter_by(student_id=student.id).count(),
            "homework": Homework.query.filter_by(student_id=student.id).count(),
        },
    }


def staff_chart_data(user, classes):
    class_ids = [item.id for item in classes]
    days = day_range(7)
    labels = []
    present_counts = []
    late_counts = []
    unmarked_counts = []
    today_present = today_late = today_absent = today_unmarked = 0
    for school_class in classes:
        headcount = len(school_class.students)
        records = Attendance.query.filter(
            Attendance.class_id == school_class.id, Attendance.day == today_ist()
        ).all()
        by_student = {}
        for row in records:
            previous = by_student.get(row.student_id)
            if previous is None or row.status == "present" or (
                row.status == "late" and previous == "absent"
            ):
                by_student[row.student_id] = row.status
        present = sum(1 for status in by_student.values() if status == "present")
        late = sum(1 for status in by_student.values() if status == "late")
        absent = sum(1 for status in by_student.values() if status == "absent")
        unmarked = max(0, headcount - present - late - absent)
        labels.append(f"{school_class.grade}-{school_class.section}")
        present_counts.append(present)
        late_counts.append(late)
        unmarked_counts.append(unmarked)
        today_present += present
        today_late += late
        today_absent += absent
        today_unmarked += unmarked
    return {
        "role": "admin" if user.is_admin else "teacher",
        "classes": {
            "labels": labels,
            "present": present_counts,
            "late": late_counts,
            "unmarked": unmarked_counts,
        },
        "today": {
            "present": today_present,
            "late": today_late,
            "absent": today_absent,
            "unmarked": today_unmarked,
        },
        "week": {
            "labels": [day.strftime("%a %d") for day in days],
            "in_school": [
                count_attendance(day, ("present", "late"), None if user.is_admin else class_ids)
                for day in days
            ],
            "absent": [
                count_attendance(day, ("absent",), None if user.is_admin else class_ids)
                for day in days
            ],
        },
    }


def attendance_stats(student):
    priority = {"present": 0, "late": 1, "absent": 2}
    by_day = {}
    for row in Attendance.query.filter_by(student_id=student.id).all():
        previous = by_day.get(row.day)
        if previous is None or priority.get(row.status, 9) < priority.get(previous, 9):
            by_day[row.day] = row.status
    present = sum(1 for status in by_day.values() if status == "present")
    late = sum(1 for status in by_day.values() if status == "late")
    absent = sum(1 for status in by_day.values() if status == "absent")
    total = len(by_day)
    percent = round((present + late) / total * 100) if total else 0
    return {"present": present, "late": late, "absent": absent, "total": total, "percent": percent}


def seed_if_empty():
    if User.query.first():
        return

    admin = User(
        username="admin",
        full_name="Super Admin",
        email="admin@school.local",
        role="super_admin",
    )
    admin.set_password("admin123")

    class_a = SchoolClass(name="Science", grade="10", section="A")
    class_b = SchoolClass(name="Mathematics", grade="8", section="B")
    db.session.add_all([admin, class_a, class_b])
    db.session.flush()

    t1_user = User(
        username="teacher1",
        full_name="Anita Sharma",
        email="anita.sharma@school.local",
        role="teacher",
    )
    t1_user.set_password("teacher123")
    t2_user = User(
        username="teacher2",
        full_name="Rahul Mehta",
        email="rahul.mehta@school.local",
        role="teacher",
    )
    t2_user.set_password("teacher123")
    db.session.add_all([t1_user, t2_user])
    db.session.flush()

    t1 = Teacher(user_id=t1_user.id, employee_id="T001", subject="Science")
    t2 = Teacher(user_id=t2_user.id, employee_id="T002", subject="Mathematics")
    db.session.add_all([t1, t2])
    db.session.flush()

    db.session.add_all(
        [
            TeacherPermission(
                teacher_id=t1.id, class_id=class_a.id, can_read=True, can_write=True, can_update=True
            ),
            TeacherPermission(
                teacher_id=t2.id, class_id=class_b.id, can_read=True, can_write=False, can_update=False
            ),
        ]
    )

    demo_students = [
        ("aarav", "Aarav Patel", "aarav.patel@school.local", class_a.id, "10A01"),
        ("diya", "Diya Khan", "diya.khan@school.local", class_a.id, "10A02"),
        ("kabir", "Kabir Singh", "kabir.singh@school.local", class_a.id, "10A03"),
        ("meera", "Meera Iyer", "meera.iyer@school.local", class_b.id, "8B01"),
        ("vihaan", "Vihaan Reddy", "vihaan.reddy@school.local", class_b.id, "8B02"),
    ]
    for username, name, email, class_id, roll in demo_students:
        user = User(username=username, full_name=name, email=email, role="student")
        user.set_password("student123")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            Student(
                user_id=user.id,
                class_id=class_id,
                admission_number=username,
                roll_number=roll,
            )
        )

    db.session.commit()


def backfill_missing_emails():
    changed = False
    for user in User.query.filter((User.email.is_(None)) | (User.email == "")).all():
        candidate = f"{user.username}@school.local"
        if email_taken(candidate, exclude_user_id=user.id):
            continue
        user.email = candidate
        changed = True
    if changed:
        db.session.commit()


def backfill_identity_numbers():
    changed = False
    for student in Student.query.all():
        if not (student.admission_number or "").strip() and student.user:
            student.admission_number = student.user.username
            changed = True
    for teacher in Teacher.query.all():
        if not (teacher.employee_id or "").strip() and teacher.user:
            teacher.employee_id = teacher.user.username
            changed = True
    if changed:
        db.session.commit()


def backfill_face_enrolled():
    changed = False
    for student in Student.query.all():
        actual = len(list_encoding_paths(student.id))
        ready = actual >= MIN_FACE_PHOTOS
        if student.face_photo_count != actual or bool(student.face_enrolled) != ready:
            student.face_photo_count = actual
            student.face_enrolled = ready
            changed = True
    if changed:
        db.session.commit()


def register_routes(app):
    @app.context_processor
    def inject_globals():
        user = current_user()
        return {
            "current_user": user,
            "today": today_ist(),
            "visible_classes": visible_classes(user) if user else [],
            "can_deactivate_user": can_deactivate_user,
            "can_access_reports": can_access_reports(user) if user else False,
            "school_name": school_title(),
            "attendance_subject": attendance_subject,
            "attendance_time": attendance_time,
        }

    @app.route("/")
    def home():
        user = current_user()
        if user and user.is_active is not False:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            user = current_user()
            if user.must_change_password:
                return redirect(url_for("change_own_password"))
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter_by(username=username).first()
            if not user:
                user = User.query.filter_by(username=username.lower()).first()
            if user and user.check_password(password):
                if user.is_active is False:
                    flash(
                        "This account is deactivated. Ask the super admin to restore access.",
                        "error",
                    )
                    return render_template("login.html")
                session["user_id"] = user.id
                session.permanent = True
                if user.must_change_password:
                    flash("This is a temporary password. Choose a new one now.", "error")
                    return redirect(url_for("change_own_password"))
                flash(f"Welcome back, {user.full_name}.", "success")
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/account/password", methods=["GET", "POST"])
    @login_required
    def change_own_password():
        user = current_user()
        if request.method == "POST":
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if len(password) < 6:
                flash("Use at least 6 characters for the new password.", "error")
                return render_template("change_password.html")
            if password != confirm:
                flash("The two passwords do not match.", "error")
                return render_template("change_password.html")
            user.set_password(password)
            user.must_change_password = False
            user.temp_password = None
            db.session.commit()
            flash("Password saved. Use this password the next time you sign in.", "success")
            return redirect(url_for("dashboard"))
        return render_template("change_password.html")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            identifier = request.form.get("identifier", "").strip()
            user = None
            if identifier:
                user = User.query.filter(
                    or_(
                        User.username == identifier.lower(),
                        User.email == normalize_email(identifier),
                    )
                ).first()
            if user and user.email and user.is_active is not False:
                raw = issue_reset_token(user)
                reset_url = url_for("reset_password", token=raw, _external=True)
                ok, error = send_reset_email(user.email, user.full_name, reset_url)
                if not ok:
                    flash(
                        "Could not send the reset email. Super admin must save outgoing email settings on Profile.",
                        "error",
                    )
                    return redirect(url_for("login"))
            flash(
                "If that account has a registered email, a one-time reset link was sent. Sign in after you set a new password.",
                "success",
            )
            return redirect(url_for("login"))
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        row = unused_reset_row(token)
        if not row:
            flash("That reset link is invalid or has already been used. Please sign in, or ask for a new link from the login page.", "error")
            return redirect(url_for("login"))
        if request.method == "POST":
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if len(password) < 6:
                flash("Use at least 6 characters for the new password.", "error")
                return render_template("reset_password.html", token=token, user=row.user)
            if password != confirm:
                flash("The two passwords do not match.", "error")
                return render_template("reset_password.html", token=token, user=row.user)
            row.user.set_password(password)
            row.user.must_change_password = False
            row.user.temp_password = None
            row.used = True
            db.session.commit()
            session.clear()
            flash("Password saved. Sign in with your new password.", "success")
            return redirect(url_for("login"))
        return render_template("reset_password.html", token=token, user=row.user)

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = current_user()
        if user.is_student:
            student = user.student_profile
            stats = attendance_stats(student)
            recent_att = (
                Attendance.query.filter_by(student_id=student.id)
                .order_by(Attendance.day.desc(), Attendance.marked_at.desc())
                .limit(8)
                .all()
            )
            recent_cw = (
                Classwork.query.filter_by(student_id=student.id)
                .order_by(Classwork.day.desc())
                .limit(6)
                .all()
            )
            recent_hw = (
                Homework.query.filter_by(student_id=student.id)
                .order_by(Homework.day.desc())
                .limit(6)
                .all()
            )
            return render_template(
                "dashboard.html",
                stats=stats,
                recent_att=recent_att,
                recent_cw=recent_cw,
                recent_hw=recent_hw,
                chart_data=student_chart_data(student, stats),
            )

        classes = visible_classes(user)
        class_cards = []
        for school_class in classes:
            present_today = (
                Attendance.query.filter(
                    Attendance.class_id == school_class.id, Attendance.day == today_ist()
                )
                .with_entities(Attendance.student_id)
                .distinct()
                .count()
            )
            perm = teacher_permission(user, school_class.id)
            class_cards.append(
                {
                    "cls": school_class,
                    "count": len(school_class.students),
                    "present": present_today,
                    "perm": perm,
                }
            )

        totals = {
            "classes": SchoolClass.query.count() if user.is_admin else len(classes),
            "students": Student.query.count()
            if user.is_admin
            else sum(len(c.students) for c in classes),
            "teachers": Teacher.query.count() if user.is_admin else 1,
            "present_today": Attendance.query.filter_by(day=today_ist())
            .with_entities(Attendance.student_id)
            .distinct()
            .count()
            if user.is_admin
            else sum(card["present"] for card in class_cards),
        }
        return render_template(
            "dashboard.html",
            class_cards=class_cards,
            totals=totals,
            chart_data=staff_chart_data(user, classes),
        )

    def class_details_taken(grade, section, exclude_id=None):
        query = SchoolClass.query.filter(
            SchoolClass.grade == grade, SchoolClass.section == section
        )
        if exclude_id:
            query = query.filter(SchoolClass.id != exclude_id)
        return query.first()

    @app.route("/classes", methods=["GET", "POST"])
    @roles_required("super_admin")
    def classes():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            grade = request.form.get("grade", "").strip()
            section = request.form.get("section", "").strip().upper()
            if not (name and grade and section):
                flash("All class fields are required.", "error")
                return redirect(url_for("classes"))
            if class_details_taken(grade, section):
                flash(f"Class {grade}-{section} already exists.", "error")
                return redirect(url_for("classes"))
            db.session.add(SchoolClass(name=name, grade=grade, section=section))
            db.session.commit()
            flash("Class added.", "success")
            return redirect(url_for("classes"))
        rows = SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()
        return render_template("classes.html", rows=rows)

    @app.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
    @roles_required("super_admin")
    def edit_class(class_id):
        school_class = db.session.get(SchoolClass, class_id)
        if not school_class:
            flash("Class not found.", "error")
            return redirect(url_for("classes"))
        next_url = safe_next(request.values.get("next"), url_for("classes"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            grade = request.form.get("grade", "").strip()
            section = request.form.get("section", "").strip().upper()
            if not (name and grade and section):
                flash("All class fields are required.", "error")
                return redirect(url_for("edit_class", class_id=class_id, next=next_url))
            if class_details_taken(grade, section, exclude_id=school_class.id):
                flash(f"Class {grade}-{section} already exists.", "error")
                return redirect(url_for("edit_class", class_id=class_id, next=next_url))
            school_class.name = name
            school_class.grade = grade
            school_class.section = section
            db.session.commit()
            flash(f"Class updated to {school_class.label}.", "success")
            return redirect(next_url)
        return render_template(
            "edit_class.html",
            school_class=school_class,
            next_url=next_url,
        )

    @app.route("/classes/<int:class_id>/delete", methods=["POST"])
    @roles_required("super_admin")
    def delete_class(class_id):
        school_class = db.session.get(SchoolClass, class_id)
        next_url = safe_next(request.form.get("next"), url_for("classes"))
        if not school_class:
            flash("Class not found.", "error")
            return redirect(next_url)
        if school_class.students:
            flash("Move or remove students before deleting this class.", "error")
            return redirect(next_url)
        TeacherPermission.query.filter_by(class_id=class_id).delete()
        label = school_class.label
        db.session.delete(school_class)
        db.session.commit()
        flash(f"{label} was removed.", "success")
        return redirect(url_for("classes"))

    @app.route("/students", methods=["GET", "POST"])
    @roles_required("super_admin")
    def students():
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            admission_number = request.form.get("admission_number", "").strip()
            username = admission_number.lower()
            email = normalize_email(request.form.get("email", ""))
            password = request.form.get("password", "student123")
            roll_number = request.form.get("roll_number", "").strip()
            class_id = request.form.get("class_id", type=int)
            if not all([full_name, admission_number, email, roll_number, class_id]):
                flash("Name, admission number, email, roll number and class are required.", "error")
                return redirect(url_for("students", show="register-student"))
            conflict = account_conflict(
                "student",
                username,
                email=email,
                full_name=full_name,
                class_id=class_id,
                roll_number=roll_number,
                admission_number=admission_number,
            )
            if conflict:
                flash(conflict, "error")
                return redirect(url_for("students", show="register-student"))
            user = User(
                username=username,
                full_name=full_name,
                email=email,
                role="student",
                temp_password=password,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            student = Student(
                user_id=user.id,
                class_id=class_id,
                admission_number=admission_number,
                roll_number=roll_number,
                face_photo_count=0,
            )
            db.session.add(student)
            db.session.commit()
            flash(
                f"{full_name} saved in {student.school_class.grade}-{student.school_class.section}. "
                f"Capture {MIN_FACE_PHOTOS} to {MAX_FACE_PHOTOS} different face photos next.",
                "success",
            )
            return redirect(url_for("students"))

        query, filters = filter_student_query(request.args)
        rows = query.order_by(SchoolClass.grade, SchoolClass.section, Student.roll_number).all()
        classes = SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()
        grades = sorted({item.grade for item in classes}, key=lambda value: (len(value), value))
        sections = sorted({item.section for item in classes})
        return render_template(
            "students.html",
            rows=rows,
            classes=classes,
            grades=grades,
            sections=sections,
            selected_class=filters["class_id"],
            selected_grade=filters["grade"],
            selected_section=filters["section"],
            search=filters["search"],
            registered_from=filters["registered_from"],
            registered_to=filters["registered_to"],
            date_filter=filters["date_filter"],
            date_label=filters["date_label"],
            password_rows=issued_password_rows(rows, "students"),
            school_name=school_title(),
            min_photos=MIN_FACE_PHOTOS,
            max_photos=MAX_FACE_PHOTOS,
            import_results=session.get("import_results")
            if (session.get("import_results") or {}).get("kind") == "students"
            else None,
        )

    @app.route("/students/import", methods=["POST"])
    @roles_required("super_admin")
    def import_students():
        upload = request.files.get("excel_file")
        default_class_id = request.form.get("class_id", type=int)
        if not upload or not upload.filename:
            flash("Choose an Excel file (.xlsx) to import students.", "error")
            return redirect(url_for("students", show="import-students"))
        try:
            ready, errors, created_classes = parse_student_rows(
                upload, default_class_id=default_class_id
            )
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("students", show="import-students"))
        created = 0
        credentials = []
        for item in ready:
            user = User(
                username=item["username"],
                full_name=item["full_name"],
                email=item["email"],
                role="student",
                must_change_password=True,
                temp_password=item["password"],
            )
            user.set_password(item["password"])
            db.session.add(user)
            db.session.flush()
            db.session.add(
                Student(
                    user_id=user.id,
                    class_id=item["class_id"],
                    admission_number=item.get("admission_number") or item["username"],
                    roll_number=item["roll_number"],
                    face_photo_count=0,
                )
            )
            credentials.append(
                {
                    "name": item["full_name"],
                    "username": item["username"],
                    "password": item["password"],
                    "detail": item.get("admission_number") or item["roll_number"],
                }
            )
            created += 1
        if created:
            db.session.commit()
            session["import_results"] = {"kind": "students", "rows": credentials}
            class_note = ""
            if created_classes:
                class_note = (
                    f" Created class{'es' if len(created_classes) != 1 else ''} "
                    f"{', '.join(created_classes)} from the import."
                )
            flash(
                f"Imported {created} student account{'s' if created != 1 else ''}. "
                "Usernames are admission numbers. Temporary passwords are shown below until they change them. Photos are optional."
                f"{class_note}",
                "success",
            )
        else:
            db.session.rollback()
            flash("No student accounts were imported.", "error")
        if errors:
            preview = " ".join(errors[:6])
            extra = f" {len(errors) - 6} more rows skipped." if len(errors) > 6 else ""
            flash(f"Skipped {len(errors)} row{'s' if len(errors) != 1 else ''}: {preview}{extra}", "error")
        if created:
            return redirect(url_for("students", show="student-passwords"))
        return redirect(url_for("students", show="import-students"))

    @app.route("/import-credentials.xlsx")
    @roles_required("super_admin")
    def import_credentials():
        results = session.get("import_results") or {}
        rows = results.get("rows") or []
        if not rows:
            flash("No imported passwords are available to download.", "error")
            return redirect(url_for("dashboard"))
        return send_file(
            credentials_workbook(results.get("kind") or "accounts", rows),
            as_attachment=True,
            download_name="imported_temporary_passwords.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/students/passwords.xlsx")
    @roles_required("super_admin")
    def student_passwords_xlsx():
        query, _filters = filter_student_query(request.args)
        rows = issued_password_rows(
            query.order_by(SchoolClass.grade, SchoolClass.section, Student.roll_number).all(),
            "students",
        )
        if not rows:
            flash("No students match that filter.", "error")
            args = request.args.to_dict()
            args["show"] = "student-passwords"
            return redirect(url_for("students", **args))
        return send_file(
            credentials_workbook("students", rows),
            as_attachment=True,
            download_name="student_passwords.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/students/export")
    @roles_required("super_admin", "teacher")
    def export_students():
        user = current_user()
        if not can_export_students(user, request.args):
            flash("You need Reports access to download that student list.", "error")
            return redirect(url_for("dashboard"))
        fmt = (request.args.get("format") or "xlsx").lower()
        if fmt not in {"xlsx", "pdf", "docx"}:
            fmt = "xlsx"
        title = (request.args.get("title") or "").strip()
        if request.args.get("save_title") and user.is_admin and title:
            set_setting("school_name", title)
            db.session.commit()
        students, filters = exportable_students(user, request.args)
        if not students:
            flash("No students match that grade or section filter.", "error")
            if request.args.get("from") == "reports":
                return redirect(url_for("reports"))
            class_id = request.args.get("class_id", type=int)
            if class_id:
                return redirect(url_for("class_view", class_id=class_id))
            return redirect(url_for("dashboard"))
        payload, filename, mimetype = build_student_export(
            students, school_title(title), filters, fmt
        )
        return send_file(payload, as_attachment=True, download_name=filename, mimetype=mimetype)

    def _reports_page_context():
        classes = SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()
        return {
            "classes": classes,
            "grades": sorted({item.grade for item in classes}, key=lambda value: (len(value), value)),
            "sections": sorted({item.section for item in classes}),
            "school_name": school_title(),
        }

    @app.route("/reports")
    @roles_required("super_admin", "teacher")
    def reports():
        user = current_user()
        if not can_access_reports(user):
            flash("The super admin must give you Reports access first.", "error")
            return redirect(url_for("dashboard"))
        return render_template("reports.html", **_reports_page_context())

    @app.route("/reports/teachers")
    @roles_required("super_admin", "teacher")
    def export_teachers():
        user = current_user()
        if not can_access_reports(user):
            flash("The super admin must give you Reports access first.", "error")
            return redirect(url_for("dashboard"))
        fmt = (request.args.get("format") or "xlsx").lower()
        if fmt not in {"xlsx", "pdf", "docx"}:
            fmt = "xlsx"
        title = (request.args.get("title") or "").strip()
        if request.args.get("save_title") and user.is_admin and title:
            set_setting("school_name", title)
            db.session.commit()
        query, filters = filter_teacher_query(request.args)
        teachers = query.order_by(User.full_name).all()
        if not teachers:
            flash("No teachers match that filter.", "error")
            return redirect(url_for("reports"))
        payload, filename, mimetype = build_teacher_export(
            teachers, school_title(title), filters, fmt
        )
        return send_file(payload, as_attachment=True, download_name=filename, mimetype=mimetype)

    @app.route("/students/import-template")
    @roles_required("super_admin")
    def student_import_template():
        return send_file(
            excel_template("students"),
            as_attachment=True,
            download_name="student_import_template.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
    @roles_required("super_admin", "teacher")
    def edit_student(student_id):
        student = db.session.get(Student, student_id)
        user = current_user()
        if not can_manage_student(user, student):
            flash("You need update permission to edit this student.", "error")
            return redirect(url_for("dashboard") if not student else url_for("class_view", class_id=student.class_id))

        next_url = safe_next(request.values.get("next"), url_for("students", class_id=student.class_id))
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            admission_number = request.form.get("admission_number", "").strip()
            username = admission_number.lower()
            email = normalize_email(request.form.get("email", ""))
            roll_number = request.form.get("roll_number", "").strip()
            class_id = request.form.get("class_id", type=int)
            if not all([full_name, admission_number, email, roll_number, class_id]):
                flash("Name, admission number, email, roll number and class are required.", "error")
                return redirect(url_for("edit_student", student_id=student.id, next=next_url))
            conflict = account_conflict(
                "student",
                username,
                email=email,
                full_name=full_name,
                class_id=class_id,
                roll_number=roll_number,
                admission_number=admission_number,
                exclude_user_id=student.user_id,
                exclude_student_id=student.id,
            )
            if conflict:
                flash(conflict, "error")
                return redirect(url_for("edit_student", student_id=student.id, next=next_url))
            new_class = db.session.get(SchoolClass, class_id)
            if not new_class:
                flash("Class not found.", "error")
                return redirect(url_for("edit_student", student_id=student.id, next=next_url))
            if class_id != student.class_id and not can(user, class_id, "update") and not user.is_admin:
                flash("You can only move a student into a class you may update.", "error")
                return redirect(url_for("edit_student", student_id=student.id, next=next_url))

            student.user.full_name = full_name
            student.user.username = username
            student.user.email = email
            student.admission_number = admission_number
            student.roll_number = roll_number
            student.class_id = class_id
            db.session.commit()
            flash(
                f"{full_name} updated · Class {new_class.grade}-{new_class.section}.",
                "success",
            )
            return redirect(next_url or url_for("students", class_id=class_id))

        classes = (
            SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all()
            if user.is_admin
            else visible_classes(user)
        )
        return render_template(
            "edit_student.html",
            student=student,
            classes=classes,
            next_url=next_url,
        )

    @app.route("/students/<int:student_id>/delete", methods=["POST"])
    @roles_required("super_admin", "teacher")
    def delete_student(student_id):
        student = db.session.get(Student, student_id)
        user = current_user()
        if not can_manage_student(user, student):
            flash("You need update permission to delete this student.", "error")
            return redirect(url_for("dashboard"))

        name = student.user.full_name
        class_id = student.class_id
        next_url = safe_next(request.form.get("next"), url_for("students", class_id=class_id))
        remove_student_record(student)
        db.session.commit()
        flash(f"{name} was removed from the register.", "success")
        return redirect(next_url)

    @app.route("/students/bulk-delete", methods=["POST"])
    @roles_required("super_admin")
    def bulk_delete_students():
        next_url = safe_next(request.form.get("next"), url_for("students"))
        ids = student_ids_from_form()
        if not ids:
            flash("Select at least one student to delete.", "error")
            return redirect(next_url)
        students = Student.query.filter(Student.id.in_(ids)).all()
        if not students:
            flash("Those students were not found.", "error")
            return redirect(next_url)
        for student in students:
            remove_student_record(student)
        db.session.commit()
        count = len(students)
        flash(
            f"Removed {count} student{'s' if count != 1 else ''} from the register.",
            "success",
        )
        return redirect(next_url)

    @app.route(
        "/users/<int:user_id>/password",
        methods=["GET", "POST"],
        endpoint="set_user_password",
    )
    @roles_required("super_admin")
    def set_user_password(user_id):
        target = db.session.get(User, user_id)
        if not target or target.role not in {"teacher", "student"}:
            flash("You can only set passwords for teachers and students.", "error")
            return redirect(url_for("dashboard"))
        next_url = safe_next(
            request.values.get("next"),
            url_for("teachers") if target.is_teacher else url_for("students"),
        )
        if request.method == "POST":
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if len(password) < 6:
                flash("Use at least 6 characters for the new password.", "error")
                return render_template(
                    "set_password.html", target=target, next_url=next_url
                )
            if password != confirm:
                flash("The two passwords do not match.", "error")
                return render_template(
                    "set_password.html", target=target, next_url=next_url
                )
            target.set_password(password)
            target.temp_password = password
            invalidate_reset_tokens(target.id)
            db.session.commit()
            flash(
                f"Password updated for {target.full_name}. They must sign in again with the new password.",
                "success",
            )
            return redirect(next_url)
        return render_template("set_password.html", target=target, next_url=next_url)

    @app.route("/users/<int:user_id>/access", methods=["POST"])
    @login_required
    def toggle_user_access(user_id):
        actor = current_user()
        target = db.session.get(User, user_id)
        next_url = safe_next(request.form.get("next"), url_for("dashboard"))
        if not can_deactivate_user(actor, target):
            flash("You cannot change access for that account.", "error")
            return redirect(next_url)
        target.is_active = not bool(target.is_active)
        db.session.commit()
        if target.is_active:
            flash(f"Access restored for {target.full_name}. They can sign in again.", "success")
        else:
            flash(
                f"{target.full_name} is deactivated and can no longer sign in.",
                "success",
            )
        return redirect(next_url)

    def _enroll_access(student):
        user = current_user()
        if not student:
            return None, redirect(url_for("dashboard"))
        if not can_take_register(user, student.class_id) and not user.is_admin:
            flash("You do not have permission to enroll this student.", "error")
            return None, redirect(url_for("class_view", class_id=student.class_id))
        return user, None

    @app.route("/students/<int:student_id>/enroll", methods=["GET"])
    @roles_required("super_admin", "teacher")
    def enroll_face(student_id):
        student = db.session.get(Student, student_id)
        _, blocked = _enroll_access(student)
        if blocked:
            return blocked
        photos = []
        folder = photo_dir(student.id)
        if folder.exists():
            photos = [
                f"uploads/faces/{student.id}/{path.name}"
                for path in sorted(folder.glob("*.jpg"))
            ]
        actual = len(list_encoding_paths(student.id))
        if actual != (student.face_photo_count or 0):
            student.face_photo_count = actual
            if actual >= MIN_FACE_PHOTOS:
                student.face_enrolled = True
            db.session.commit()
        count = student.face_photo_count or 0
        return render_template(
            "enroll.html",
            student=student,
            photos=photos,
            count=count,
            min_photos=MIN_FACE_PHOTOS,
            max_photos=MAX_FACE_PHOTOS,
            prompt=pose_prompt(count),
        )

    @app.route("/api/enroll/<int:student_id>", methods=["POST"])
    @roles_required("super_admin", "teacher")
    def api_enroll(student_id):
        student = db.session.get(Student, student_id)
        _, blocked = _enroll_access(student)
        if blocked:
            return jsonify({"ok": False, "error": "You cannot enroll this student."}), 403
        files = request.files.getlist("images") or []
        single = request.files.get("image")
        if single and single.filename:
            files.append(single)
        if not files:
            return jsonify({"ok": False, "error": "No image received."}), 400

        last = None
        saved = 0
        skipped_similar = 0
        skipped_face = 0
        for item in files:
            if not item or not item.filename:
                continue
            last = store_face_sample(student, item.read())
            if last.get("ok"):
                saved += 1
            elif last.get("error") == "similar":
                skipped_similar += 1
            elif last.get("error") == "no_face":
                skipped_face += 1
            elif last.get("error") == "max":
                break

        if last is None:
            return jsonify({"ok": False, "error": "No image received."}), 400
        if saved == 0 and last.get("error") == "no_face":
            return jsonify(
                {
                    "ok": False,
                    "error": "No face found. Face the camera and try again.",
                    "count": last.get("count", student.face_photo_count or 0),
                }
            ), 400
        if saved == 0 and last.get("error") == "similar":
            return jsonify(
                {
                    "ok": False,
                    "error": "That photo is too similar. Change angle, distance or expression.",
                    "count": last["count"],
                    "prompt": last.get("prompt"),
                }
            ), 400
        if saved == 0 and last.get("error") == "max":
            return jsonify(
                {
                    "ok": False,
                    "error": f"Maximum {MAX_FACE_PHOTOS} photos reached.",
                    "count": last["count"],
                    "ready": True,
                }
            ), 400

        db.session.refresh(student)
        return jsonify(
            {
                "ok": True,
                "saved": saved,
                "skipped_similar": skipped_similar,
                "skipped_face": skipped_face,
                "count": student.face_photo_count,
                "min": MIN_FACE_PHOTOS,
                "max": MAX_FACE_PHOTOS,
                "ready": student.face_enrolled,
                "prompt": pose_prompt(student.face_photo_count),
                "photo": last.get("photo"),
                "message": (
                    f"{student.user.full_name} is ready for face scan."
                    if student.face_enrolled
                    else f"Need {MIN_FACE_PHOTOS - student.face_photo_count} more different photos."
                ),
            }
        )

    @app.route("/teachers", methods=["GET", "POST"])
    @roles_required("super_admin")
    def teachers():
        if request.method == "POST":
            action = request.form.get("action", "add_teacher")
            can_read = request.form.get("can_read") == "on"
            can_write = request.form.get("can_write") == "on"
            can_update = request.form.get("can_update") == "on"
            can_deactivate = request.form.get("can_deactivate") == "on"
            class_ids = class_ids_from_form()

            if action == "grant":
                teacher_id = request.form.get("teacher_id", type=int)
                if not teacher_id or not class_ids:
                    flash("Choose a teacher and at least one class.", "error")
                    return redirect(url_for("teachers", show="grant-permission"))
                count = apply_class_permissions(
                    teacher_id, class_ids, can_read, can_write, can_update, can_deactivate
                )
                db.session.commit()
                flash(
                    f"Permission saved on {count} class{'es' if count != 1 else ''}.",
                    "success",
                )
                return redirect(url_for("teachers"))

            if action == "reports":
                teacher_id = request.form.get("teacher_id", type=int)
                teacher = db.session.get(Teacher, teacher_id)
                if not teacher:
                    flash("Teacher not found.", "error")
                    return redirect(url_for("teachers", show="grant-permission"))
                teacher.can_reports = request.form.get("can_reports") == "on"
                db.session.commit()
                flash(
                    f"Reports access {'given to' if teacher.can_reports else 'removed from'} {teacher.user.full_name}.",
                    "success",
                )
                return redirect(url_for("teachers"))

            if action == "toggle_reports":
                teacher_id = request.form.get("teacher_id", type=int)
                teacher = db.session.get(Teacher, teacher_id)
                if not teacher:
                    flash("Teacher not found.", "error")
                    return redirect(url_for("teachers"))
                teacher.can_reports = not bool(teacher.can_reports)
                db.session.commit()
                flash(
                    f"Reports access {'given to' if teacher.can_reports else 'removed from'} {teacher.user.full_name}.",
                    "success",
                )
                return redirect(safe_next(request.form.get("next"), url_for("teachers")))

            full_name = request.form.get("full_name", "").strip()
            employee_id = request.form.get("employee_id", "").strip()
            username = (request.form.get("username", "").strip() or employee_id).lower()
            email = normalize_email(request.form.get("email", ""))
            password = request.form.get("password", "teacher123")
            subject = request.form.get("subject", "").strip()
            if not full_name or not employee_id or not email:
                flash("Name, employee ID and email are required.", "error")
                return redirect(url_for("teachers", show="register-teacher"))
            conflict = account_conflict(
                "teacher",
                username,
                email=email,
                full_name=full_name,
                employee_id=employee_id,
            )
            if conflict:
                flash(conflict, "error")
                return redirect(url_for("teachers", show="register-teacher"))
            user = User(
                username=username,
                full_name=full_name,
                email=email,
                role="teacher",
                temp_password=password,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            teacher = Teacher(
                user_id=user.id,
                employee_id=employee_id,
                subject=subject,
                can_reports=request.form.get("can_reports") == "on",
            )
            db.session.add(teacher)
            db.session.flush()
            if class_ids:
                apply_class_permissions(
                    teacher.id, class_ids, can_read, can_write, can_update, can_deactivate
                )
            db.session.commit()
            if class_ids:
                count = len(class_ids)
                flash(
                    f"Teacher added with permission on {count} class{'es' if count != 1 else ''}.",
                    "success",
                )
            else:
                flash("Teacher added. Grant a class permission below.", "success")
            return redirect(url_for("teachers"))

        query, filters = filter_teacher_query(request.args)
        rows = query.order_by(User.full_name).all()
        return render_template(
            "teachers.html",
            rows=rows,
            all_teachers=Teacher.query.join(User).order_by(User.full_name).all(),
            classes=SchoolClass.query.order_by(SchoolClass.grade, SchoolClass.section).all(),
            search=filters["search"],
            registered_from=filters["registered_from"],
            registered_to=filters["registered_to"],
            date_filter=filters["date_filter"],
            date_label=filters["date_label"],
            password_rows=issued_password_rows(rows, "teachers"),
            import_results=session.get("import_results")
            if (session.get("import_results") or {}).get("kind") == "teachers"
            else None,
        )

    @app.route("/teachers/import", methods=["POST"])
    @roles_required("super_admin")
    def import_teachers():
        upload = request.files.get("excel_file")
        default_class_id = request.form.get("class_id", type=int)
        if not upload or not upload.filename:
            flash("Choose an Excel file (.xlsx) to import teachers.", "error")
            return redirect(url_for("teachers", show="import-teachers"))
        try:
            ready, errors = parse_teacher_rows(upload, default_class_id=default_class_id)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("teachers", show="import-teachers"))
        created = 0
        credentials = []
        for item in ready:
            user = User(
                username=item["username"],
                full_name=item["full_name"],
                email=item["email"],
                role="teacher",
                must_change_password=True,
                temp_password=item["password"],
            )
            user.set_password(item["password"])
            db.session.add(user)
            db.session.flush()
            teacher = Teacher(
                user_id=user.id,
                employee_id=item.get("employee_id") or item["username"],
                subject=item["subject"],
            )
            db.session.add(teacher)
            db.session.flush()
            if item["class_id"]:
                apply_class_permission(teacher.id, item["class_id"], True, False, False, False)
            credentials.append(
                {
                    "name": item["full_name"],
                    "username": item["username"],
                    "password": item["password"],
                    "detail": item.get("employee_id") or item["subject"] or "—",
                }
            )
            created += 1
        if created:
            db.session.commit()
            session["import_results"] = {"kind": "teachers", "rows": credentials}
            flash(
                f"Imported {created} teacher account{'s' if created != 1 else ''}. "
                "Each teacher can only read student data. Temporary passwords are shown below until they change them. Photos are not required.",
                "success",
            )
        else:
            db.session.rollback()
            flash("No teacher accounts were imported.", "error")
        if errors:
            preview = " ".join(errors[:6])
            extra = f" {len(errors) - 6} more rows skipped." if len(errors) > 6 else ""
            flash(f"Skipped {len(errors)} row{'s' if len(errors) != 1 else ''}: {preview}{extra}", "error")
        if created:
            return redirect(url_for("teachers", show="teacher-passwords"))
        return redirect(url_for("teachers", show="import-teachers"))

    @app.route("/teachers/import-template")
    @roles_required("super_admin")
    def teacher_import_template():
        return send_file(
            excel_template("teachers"),
            as_attachment=True,
            download_name="teacher_import_template.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/teachers/passwords.xlsx")
    @roles_required("super_admin")
    def teacher_passwords_xlsx():
        query, _filters = filter_teacher_query(request.args)
        rows = issued_password_rows(query.order_by(User.full_name).all(), "teachers")
        if not rows:
            flash("No teachers match that filter.", "error")
            args = request.args.to_dict()
            args["show"] = "teacher-passwords"
            return redirect(url_for("teachers", **args))
        return send_file(
            credentials_workbook("teachers", rows),
            as_attachment=True,
            download_name="teacher_passwords.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.route("/permissions", methods=["GET", "POST"])
    @roles_required("super_admin")
    def permissions():
        return redirect(url_for("teachers"))

    @app.route("/attendance")
    @roles_required("super_admin", "teacher")
    def attendance_log():
        user = current_user()
        classes = visible_classes(user)
        class_id = request.args.get("class_id", type=int)
        subject = (request.args.get("subject") or "").strip()
        day_from = parse_filter_date(request.args.get("day"))
        day_to = parse_filter_date(request.args.get("until"))
        if not day_from and not day_to:
            day_from = day_to = today_ist()
        elif day_from and not day_to:
            day_to = day_from
        elif day_to and not day_from:
            day_from = day_to
        if day_from > day_to:
            day_from, day_to = day_to, day_from
        allowed_ids = [item.id for item in classes]
        query = (
            Attendance.query.join(Student)
            .join(User)
            .outerjoin(SchoolClass, Attendance.class_id == SchoolClass.id)
        )
        if not user.is_admin:
            if not allowed_ids:
                query = query.filter(Attendance.id == 0)
            else:
                query = query.filter(Attendance.class_id.in_(allowed_ids))
        if class_id:
            if not user.is_admin and class_id not in allowed_ids:
                flash("You do not have permission to view that class.", "error")
                return redirect(url_for("attendance_log"))
            query = query.filter(Attendance.class_id == class_id)
        subjects = subject_choices(classes)
        if subject:
            query = query.filter(Attendance.subject == subject)
        query = query.filter(Attendance.day >= day_from, Attendance.day <= day_to)
        rows = query.order_by(
            Attendance.day.desc(),
            SchoolClass.grade,
            SchoolClass.section,
            Attendance.marked_at.asc(),
            Student.roll_number,
        ).all()
        return render_template(
            "attendance.html",
            rows=rows,
            classes=classes,
            subjects=subjects,
            selected_class=class_id,
            selected_subject=subject,
            day_from=day_from.isoformat(),
            day_to=day_to.isoformat(),
        )

    @app.route("/permissions/<int:perm_id>/delete", methods=["POST"])
    @roles_required("super_admin")
    def delete_permission(perm_id):
        perm = db.session.get(TeacherPermission, perm_id)
        if perm:
            db.session.delete(perm)
            db.session.commit()
            flash("Permission removed.", "success")
        return redirect(url_for("teachers"))

    @app.route("/class/<int:class_id>")
    @login_required
    def class_view(class_id):
        user = current_user()
        school_class = db.session.get(SchoolClass, class_id)
        if not school_class:
            flash("Class not found.", "error")
            return redirect(url_for("dashboard"))
        if user.is_student:
            return redirect(url_for("my_records"))
        elif not can(user, class_id, "read"):
            flash("You do not have permission to view this class.", "error")
            return redirect(url_for("dashboard"))

        perm = teacher_permission(user, class_id)
        students = (
            Student.query.filter_by(class_id=class_id)
            .join(User)
            .order_by(Student.roll_number)
            .all()
        )
        today_records = Attendance.query.filter(
            Attendance.student_id.in_([s.id for s in students] or [0]),
            Attendance.day == today_ist(),
        ).order_by(Attendance.marked_at.asc()).all()
        today_map = {}
        for record in today_records:
            today_map.setdefault(record.student_id, []).append(record)
        subject = class_subject(school_class)
        cutoff = session_cutoff()
        session_map = {}
        for record in today_records:
            if attendance_subject(record) == subject and record.marked_at and record.marked_at >= cutoff:
                session_map.setdefault(record.student_id, record)
        work_map = {}
        for student in students:
            cw = Classwork.query.filter_by(student_id=student.id, day=today_ist()).first()
            hw = Homework.query.filter_by(student_id=student.id, day=today_ist()).first()
            work_map[student.id] = {"cw": cw, "hw": hw}
        return render_template(
            "class_view.html",
            school_class=school_class,
            students=students,
            today_map=today_map,
            work_map=work_map,
            perm=perm,
            class_subject=subject,
            session_map=session_map,
        )

    @app.route("/attendance/<int:record_id>", methods=["POST"])
    @login_required
    def update_attendance(record_id):
        user = current_user()
        record = db.session.get(Attendance, record_id)
        if not record:
            flash("Record not found.", "error")
            return redirect(url_for("dashboard"))
        if user.is_student or not can(user, record.student.class_id, "update"):
            flash("You need update permission to change attendance.", "error")
            return redirect(url_for("class_view", class_id=record.student.class_id))
        status = request.form.get("status", "present")
        if status in {"present", "absent", "late"}:
            record.status = status
            record.marked_by_id = user.id
            db.session.commit()
            flash("Attendance updated.", "success")
        return redirect(url_for("class_view", class_id=record.student.class_id))

    @app.route("/attendance/manual", methods=["POST"])
    @login_required
    def manual_attendance():
        user = current_user()
        student_id = request.form.get("student_id", type=int)
        status = request.form.get("status", "present")
        student = db.session.get(Student, student_id)
        if not student:
            flash("Student not found.", "error")
            return redirect(url_for("dashboard"))
        if user.is_student or not can_take_register(user, student.class_id):
            flash("You need write or update permission to mark attendance.", "error")
            return redirect(url_for("class_view", class_id=student.class_id))
        subject = class_subject(student.school_class, request.form.get("subject"))
        record, created = mark_attendance(
            student,
            user,
            "manual",
            subject=subject,
            status=status if status in {"present", "absent", "late"} else "present",
            school_class=student.school_class,
        )
        if created:
            flash(
                f"Attendance saved for {student.user.full_name} · {attendance_subject(record)} at {attendance_time(record)}.",
                "success",
            )
        else:
            flash(
                f"{student.user.full_name} is already marked {record.status} for {attendance_subject(record)} in this class hour. Earlier record was kept.",
                "success",
            )
        return redirect(url_for("class_view", class_id=student.class_id))

    @app.route("/scan")
    @roles_required("super_admin", "teacher")
    def scan():
        user = current_user()
        writable = [
            item
            for item in visible_classes(user)
            if user.is_admin or can_take_register(user, item.id)
        ]
        if not writable and not user.is_admin:
            flash("You need write or update permission on a class before you can scan.", "error")
            return redirect(url_for("dashboard"))
        scan_students = (
            Student.query.filter(Student.class_id.in_([c.id for c in writable] or [0]))
            .join(User)
            .filter(User.is_active.is_(True))
            .order_by(Student.roll_number)
            .all()
        )
        enrolled_ready = sum(1 for item in scan_students if item.face_enrolled)
        selected_class = request.args.get("class_id", type=int)
        if selected_class and selected_class not in [item.id for item in writable]:
            selected_class = writable[0].id if writable else None
        elif not selected_class and writable:
            selected_class = writable[0].id
        return render_template(
            "scan.html",
            writable=writable,
            scan_students=scan_students,
            enrolled_ready=enrolled_ready,
            min_photos=MIN_FACE_PHOTOS,
            max_photos=MAX_FACE_PHOTOS,
            selected_class=selected_class,
        )

    @app.route("/api/scan", methods=["POST"])
    @roles_required("super_admin", "teacher")
    def api_scan():
        user = current_user()
        mode = request.form.get("mode", "attendance")
        image = request.files.get("image")
        if not image:
            return jsonify({"ok": False, "error": "No image received."}), 400

        live = request.form.get("live") == "1"
        file_bytes = image.read()
        chosen_id = request.form.get("student_id", type=int)
        allowed_ids = None if user.is_admin else register_class_ids(user)
        student_id, score, status = match_student(file_bytes, enrolled_faces(allowed_ids))

        if chosen_id and status != "ok" and (mode == "work" or not live):
            chosen = db.session.get(Student, chosen_id)
            if chosen and can_take_register(user, chosen.class_id):
                student_id = chosen_id
                score = max(score or 0.0, 1.0)
                status = "ok"
        if status == "no_face":
            payload = {"ok": False, "error": "No face found. Face the camera and try again.", "quiet": live}
            return jsonify(payload), 200 if live else 400
        if status == "no_match" or not student_id:
            payload = {
                "ok": False,
                "error": "Face not recognised among your students. Enroll more photos or choose the student below.",
                "score": round(score, 3),
                "quiet": live,
            }
            return jsonify(payload), 200 if live else 404

        student = db.session.get(Student, student_id)
        if not student:
            return jsonify({"ok": False, "error": "Student not found."}), 404
        if student.user and student.user.is_active is False:
            return jsonify({"ok": False, "error": "This student account is deactivated."}), 403
        if not can_take_register(user, student.class_id):
            payload = {
                "ok": False,
                "error": "This face is not in a class you can mark.",
                "quiet": live,
            }
            return jsonify(payload), 200 if live else 403

        school_class = student.school_class
        payload = {
            "ok": True,
            "student": {
                "id": student.id,
                "name": student.user.full_name,
                "roll": student.roll_number,
                "class": school_class.label,
                "grade": school_class.grade,
                "section": school_class.section,
                "subject": school_class.name,
                "photo": student.face_image_path,
            },
            "score": round(score, 3),
        }

        if mode == "attendance":
            lesson = lesson_class_for_user(user, request.form.get("class_id", type=int))
            subject = class_subject(lesson or student.school_class)
            record, created = mark_attendance(
                student,
                user,
                "face_scan",
                subject=subject,
                school_class=lesson or student.school_class,
            )
            when = attendance_time(record)
            payload["action"] = "attendance"
            payload["status"] = record.status
            payload["subject"] = attendance_subject(record)
            payload["student"]["subject"] = attendance_subject(record)
            payload["message"] = (
                f"{student.user.full_name} · Class {school_class.grade}-{school_class.section} · {attendance_subject(record)} marked present at {when}."
                if created
                else f"{student.user.full_name} · already marked {record.status} for {attendance_subject(record)} in this class hour at {when}. Earlier record kept."
            )
            return jsonify(payload)

        token = secrets.token_hex(8)
        work_name = f"{student.id}_{today_ist().isoformat()}_{token}.jpg"
        work_path = WORK_DIR / work_name
        save_preview(work_path, file_bytes)
        rel = f"uploads/work/{work_name}"
        mark_work(student, user, rel)
        payload["action"] = "work"
        payload["message"] = (
            f"Classwork and homework submitted for {student.user.full_name}."
        )
        payload["image"] = rel
        return jsonify(payload)

    @app.route("/my-records")
    @roles_required("student")
    def my_records():
        student = current_user().student_profile
        attendance = (
            Attendance.query.filter_by(student_id=student.id)
            .order_by(Attendance.day.desc(), Attendance.marked_at.desc())
            .all()
        )
        classwork = (
            Classwork.query.filter_by(student_id=student.id).order_by(Classwork.day.desc()).all()
        )
        homework = (
            Homework.query.filter_by(student_id=student.id).order_by(Homework.day.desc()).all()
        )
        return render_template(
            "my_records.html",
            student=student,
            stats=attendance_stats(student),
            attendance=attendance,
            classwork=classwork,
            homework=homework,
        )

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        user = current_user()
        if request.method == "POST":
            if request.form.get("action") == "school" and user.is_admin:
                set_setting("school_name", request.form.get("school_name", "").strip())
                db.session.commit()
                flash("School name saved. Downloads will use this heading unless you type a custom name.", "success")
                return redirect(url_for("profile"))
            if request.form.get("action") == "smtp" and user.is_admin:
                set_setting("smtp_host", request.form.get("smtp_host", "").strip())
                set_setting("smtp_port", request.form.get("smtp_port", "587").strip())
                set_setting("smtp_username", request.form.get("smtp_username", "").strip())
                password = request.form.get("smtp_password", "").strip()
                if password:
                    set_setting("smtp_password", password)
                set_setting("smtp_from", request.form.get("smtp_from", "").strip())
                set_setting("smtp_use_tls", "1" if request.form.get("smtp_use_tls") == "on" else "0")
                db.session.commit()
                flash("Outgoing email settings saved. Reset links will be sent from this mailbox.", "success")
                return redirect(url_for("profile"))

            email = normalize_email(request.form.get("email", ""))
            if not email:
                flash("A registered email is required for password reset.", "error")
                return redirect(url_for("profile"))
            if email_taken(email, exclude_user_id=user.id):
                flash("That email is already registered.", "error")
                return redirect(url_for("profile"))
            user.email = email
            db.session.commit()
            flash("Email saved. Password reset mail will go to this address.", "success")
            return redirect(url_for("profile"))
        return render_template(
            "profile.html",
            mail=mail_settings(),
            mail_ready=mail_configured(),
            school_name=school_title(),
        )


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
