from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

try:
    IST = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_ist():
    return datetime.now(IST)


def today_ist(when=None):
    stamp = when or now_ist()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp.astimezone(IST).date()


def naive_ist(when=None):
    stamp = when or now_ist()
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(IST).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(20), nullable=False)  # super_admin, teacher, student
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    temp_password = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student_profile = db.relationship("Student", back_populates="user", uselist=False)
    teacher_profile = db.relationship("Teacher", back_populates="user", uselist=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "super_admin"

    @property
    def is_teacher(self):
        return self.role == "teacher"

    @property
    def is_student(self):
        return self.role == "student"


class SchoolClass(db.Model):
    __tablename__ = "classes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    section = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    students = db.relationship("Student", back_populates="school_class")
    permissions = db.relationship("TeacherPermission", back_populates="school_class")

    @property
    def label(self):
        return f"{self.grade}-{self.section} · {self.name}"


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    admission_number = db.Column(db.String(40))
    roll_number = db.Column(db.String(20), nullable=False)
    face_image_path = db.Column(db.String(255))
    face_enrolled = db.Column(db.Boolean, default=False)
    face_photo_count = db.Column(db.Integer, default=0)

    user = db.relationship("User", back_populates="student_profile")
    school_class = db.relationship("SchoolClass", back_populates="students")
    attendance = db.relationship("Attendance", back_populates="student")
    classwork = db.relationship("Classwork", back_populates="student")
    homework = db.relationship("Homework", back_populates="student")


class Teacher(db.Model):
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    employee_id = db.Column(db.String(40))
    subject = db.Column(db.String(80), default="")
    can_reports = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User", back_populates="teacher_profile")
    permissions = db.relationship("TeacherPermission", back_populates="teacher")


class TeacherPermission(db.Model):
    __tablename__ = "teacher_permissions"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    can_read = db.Column(db.Boolean, default=True)
    can_write = db.Column(db.Boolean, default=False)
    can_update = db.Column(db.Boolean, default=False)
    can_deactivate = db.Column(db.Boolean, default=False)

    teacher = db.relationship("Teacher", back_populates="permissions")
    school_class = db.relationship("SchoolClass", back_populates="permissions")

    __table_args__ = (db.UniqueConstraint("teacher_id", "class_id", name="uq_teacher_class"),)


class Attendance(db.Model):
    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"))
    day = db.Column(db.Date, default=today_ist, nullable=False)
    period = db.Column(db.String(20), default="1", nullable=False)
    subject = db.Column(db.String(80), default="", nullable=False)
    status = db.Column(db.String(20), default="present")  # present, absent, late
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    source = db.Column(db.String(20), default="face_scan")
    marked_at = db.Column(db.DateTime, default=naive_ist)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("Student", back_populates="attendance")
    school_class = db.relationship("SchoolClass")
    marked_by = db.relationship("User")

    __table_args__ = (
        db.Index("idx_attendance_student_subject_day", "student_id", "subject", "day"),
    )


class Classwork(db.Model):
    __tablename__ = "classwork"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    day = db.Column(db.Date, default=date.today, nullable=False)
    title = db.Column(db.String(120), default="Classwork")
    status = db.Column(db.String(20), default="submitted")  # submitted, pending
    image_path = db.Column(db.String(255))
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("Student", back_populates="classwork")
    marked_by = db.relationship("User")


class Homework(db.Model):
    __tablename__ = "homework"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    day = db.Column(db.Date, default=date.today, nullable=False)
    title = db.Column(db.String(120), default="Homework")
    status = db.Column(db.String(20), default="submitted")
    image_path = db.Column(db.String(255))
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("Student", back_populates="homework")
    marked_by = db.relationship("User")


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default="")
