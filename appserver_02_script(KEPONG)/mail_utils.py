import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config_loader import config_loader


def send_alert(subject, html_body):
    config = config_loader.get_config()
    mail = config["mail"]

    full_subject = f"{mail.get('subject_prefix', '').strip()} {subject}"

    msg = MIMEMultipart("alternative")
    msg["From"] = mail["from_email"]
    msg["To"] = ", ".join(mail["to_emails"])
    msg["Subject"] = full_subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(mail["smtp_server"], mail["smtp_port"], timeout=15) as server:
            # IMPORTANT: No EHLO, No STARTTLS, No LOGIN
            server.sendmail(
                mail["from_email"],
                mail["to_emails"],
                msg.as_string()
            )

        print(f"[MAIL] Sent alert: {full_subject}")

    except Exception as e:
        print(f"[MAIL ERROR] {e}")