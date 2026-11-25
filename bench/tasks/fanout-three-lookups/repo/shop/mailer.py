import smtplib
from email.message import EmailMessage

from shop import settings


def send(to, subject, body):
    message = EmailMessage()
    message["From"] = settings.get("mail", "sender")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.get("mail", "smtp_host")) as smtp:
        smtp.send_message(message)
