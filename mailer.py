"""Gmail SMTP sender. Credentials come from environment variables (.env).

send_mail() runs in a background thread so a slow SMTP server never
makes a page hang. It never raises: failures are printed to the terminal.
"""
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape


def app_url():
    return os.getenv("APP_URL", "http://localhost:5000").rstrip("/")


def email_html(heading, body_html, button_text=None, path="/"):
    """Branded HTML email. body_html must already be escaped/safe."""
    button = ""
    if button_text:
        button = (
            f'<p style="margin:26px 0 6px"><a href="{app_url()}{path}" '
            'style="background:#7a0000;color:#fff;padding:12px 26px;border-radius:999px;'
            f'text-decoration:none;font-weight:600">{escape(button_text)}</a></p>'
        )
    return (
        '<div style="font-family:Arial,sans-serif;background:#f1f1f1;padding:24px">'
        '<div style="max-width:560px;margin:auto;background:#fff;border-radius:14px;overflow:hidden">'
        '<div style="background:#4a0000;color:#fff;padding:18px 24px;font-size:20px;'
        'font-family:Georgia,serif;font-style:italic">The Channel Desk</div>'
        f'<div style="padding:24px;color:#222;line-height:1.6"><h2 style="margin-top:0">{escape(heading)}</h2>'
        f"{body_html}{button}</div>"
        '<div style="padding:14px 24px;font-size:12px;color:#888;border-top:1px solid #eee">'
        "By Truth, We Shape Lives.</div></div></div>"
    )


def _deliver(recipients, subject, html_body):
    username = os.getenv("MAIL_USERNAME")
    password = os.getenv("MAIL_PASSWORD")
    if not username or not password:
        print(f"[mail disabled: set MAIL_USERNAME / MAIL_PASSWORD in .env] {subject} -> {recipients}")
        return
    sender = f"{os.getenv('MAIL_FROM_NAME', 'The Channel Desk')} <{username}>"
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            for to in recipients:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = sender
                msg["To"] = to
                msg.attach(MIMEText(html_body, "html"))
                smtp.sendmail(username, [to], msg.as_string())
        print(f"[mail sent] {subject} -> {len(recipients)} recipient(s)")
    except Exception as exc:  # mail must never break a request
        print(f"[mail error] {exc}")


def send_mail(to, subject, html_body):
    """`to` may be one address or a list. Sends in the background."""
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r for r in recipients if r]
    if not recipients:
        return
    threading.Thread(target=_deliver, args=(recipients, subject, html_body), daemon=True).start()