import smtplib
import time
import os
import glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config_loader import config_loader

# Load config
config = config_loader.get_config()
log_config = config.get("log", {})
mail_config = config.get("mail_report", {})

# Extract log config
REPORT_DIR = log_config.get("directory", "/tmp/monitor_logs")
FILENAME_PREFIX = log_config.get("filename_prefix", "report")
EXTENSION = log_config.get("extension", ".txt")

# Extract mail config
smtp_server = mail_config.get("smtp_server")
smtp_port = mail_config.get("smtp_port", 25)
smtp_username = mail_config.get("username")  # optional
smtp_password = mail_config.get("password")  # optional
use_tls = mail_config.get("use_tls", False)  # optional
from_email = mail_config.get("from_email")
to_emails = mail_config.get("to_emails", [])
subject_prefix = mail_config.get("subject_prefix", "").strip()

# Email subject and body
subject = f"{subject_prefix}"
body = """
Hi Team,<br><br>
Please find attached the latest system monitoring report.<br><br>
Regards,<br>
Automation Monitor
"""


def get_latest_report():
    try:
        search_path = os.path.join(REPORT_DIR, f"{FILENAME_PREFIX}_*{EXTENSION}")
        report_files = glob.glob(search_path)
        if not report_files:
            print("No report files found.")
            return None
        return max(report_files, key=os.path.getmtime)
    except Exception as e:
        print(f"[ERROR] Finding report: {str(e)}")
        return None


def wait_for_complete_file(file_path, timeout=120):
    prev_size = -1
    elapsed_time = 0
    while elapsed_time < timeout:
        if os.path.exists(file_path):
            current_size = os.path.getsize(file_path)
            if current_size == prev_size:
                return True
            prev_size = current_size
        time.sleep(5)
        elapsed_time += 5
    print(f"Warning: Report file {file_path} might still be incomplete.")
    return False


def send_email_with_retry(retries=3, delay=5):

    report_file = get_latest_report()
    if not report_file:
        print("No report file found to send.")
        return

    if not wait_for_complete_file(report_file):
        print("Report file still growing. Email skipped.")
        return

    for attempt in range(retries):
        try:
            msg = MIMEMultipart()
            msg["From"] = from_email
            msg["To"] = ", ".join(to_emails)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))

            # Attach report
            with open(report_file, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(report_file)}"
                )
                msg.attach(part)

            # Send email
            with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as server:

                # Use TLS only if configured
                if use_tls:
                    try:
                        server.starttls()
                    except Exception as e:
                        print(f"[WARNING] STARTTLS failed: {e}")

                # Login only if credentials provided
                if smtp_username and smtp_password:
                    server.login(smtp_username, smtp_password)

                server.sendmail(from_email, to_emails, msg.as_string())

            print(f"✅ Email sent successfully to: {', '.join(to_emails)}")
            return

        except smtplib.SMTPException as smtp_err:
            print(f"❌ Attempt {attempt + 1} failed: {smtp_err}")
            if attempt < retries - 1:
                time.sleep(delay * (2 ** attempt))
            else:
                print("❌ Email sending failed after all retries.")

        except Exception as e:
            print(f"[ERROR] Failed to send email: {str(e)}")
            return


if __name__ == "__main__":
    send_email_with_retry()

