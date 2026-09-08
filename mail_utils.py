import smtplib
import ssl
from email.message import EmailMessage

from models import AppSetting, db


def get_setting(key, default=""):
    row = db.session.get(AppSetting, key)
    if not row or row.value is None:
        return default
    return row.value


def set_setting(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value or "")
        db.session.add(row)
    else:
        row.value = value or ""


def mail_settings():
    return {
        "host": get_setting("smtp_host", "smtp.gmail.com"),
        "port": int(get_setting("smtp_port", "587") or 587),
        "username": get_setting("smtp_username"),
        "password": get_setting("smtp_password"),
        "sender": get_setting("smtp_from") or get_setting("smtp_username"),
        "use_tls": get_setting("smtp_use_tls", "1") != "0",
    }


def mail_configured():
    settings = mail_settings()
    return bool(settings["host"] and settings["username"] and settings["password"] and settings["sender"])


def send_reset_email(to_email, full_name, reset_url):
    settings = mail_settings()
    if not mail_configured():
        return False, "Outgoing email is not configured."

    message = EmailMessage()
    message["Subject"] = "Reset your Attendance Register password"
    message["From"] = settings["sender"]
    message["To"] = to_email
    message.set_content(
        f"Hello {full_name},\n\n"
        "Use this link to set a new password. It works only once. "
        "After you save the new password, sign in again.\n\n"
        f"{reset_url}\n\n"
        "If you did not ask to reset your password, ignore this email.\n"
    )
    try:
        if settings["use_tls"]:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(settings["username"], settings["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=20) as smtp:
                smtp.login(settings["username"], settings["password"])
                smtp.send_message(message)
        return True, ""
    except Exception as error:
        return False, str(error)
