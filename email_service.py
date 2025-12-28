import os
import io
import asyncio
from typing import List, Optional
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pydantic import EmailStr
from datetime import datetime

from i18n import TRANSLATIONS

# --- Configuration ---
# Infer TLS/SSL from Port if not explicitly set
# Port 587 usually STARTTLS, 465 usually SSL/TLS
mail_port = int(os.environ.get("MAIL_PORT", 587))
use_tls = os.environ.get("MAIL_STARTTLS", "").lower() == "true"
use_ssl = os.environ.get("MAIL_SSL_TLS", "").lower() == "true"

# Auto-inference if not set
if "MAIL_STARTTLS" not in os.environ and "MAIL_SSL_TLS" not in os.environ:
    if mail_port == 587:
        use_tls = True
        use_ssl = False
    elif mail_port == 465:
        use_tls = False
        use_ssl = True

conf = ConnectionConfig(
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
    MAIL_FROM=os.environ.get("MAIL_FROM", "noreply@example.com"),
    MAIL_FROM_NAME=os.environ.get("MAIL_FROM_NAME", "Grade System"),
    MAIL_PORT=mail_port,
    MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_STARTTLS=use_tls,
    MAIL_SSL_TLS=use_ssl,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)

fastmail = FastMail(conf)

# Jinja Setup for PDF
template_env = Environment(loader=FileSystemLoader("templates"))
template_env.globals['translations'] = TRANSLATIONS

def generate_pdf(report_data: dict, lang: str = "en") -> bytes:
    """
    Generates a PDF byte stream from the student report data.
    Blocking function, should be run in a thread.
    """
    template = template_env.get_template("student_report_pdf.html")

    # Render HTML
    html_content = template.render(
        report=report_data,
        lang=lang,
        translations=TRANSLATIONS,
        now_utc=datetime.utcnow()
    )

    # Convert to PDF
    # We use a base_url so WeasyPrint can find relative assets if needed (though we use inline styles mostly)
    # Using a fake base_url for now or current dir
    pdf_bytes = HTML(string=html_content, base_url=".").write_pdf()

    return pdf_bytes

async def send_grade_email(
    student_email: str,
    student_name: str,
    pdf_bytes: bytes,
    lang: str = "en"
):
    """
    Sends an email with the grade report PDF attached.
    """

    subject_map = {
        "en": f"Grade Report for {student_name}",
        "zh": f"{student_name} 的成績單"
    }

    body_map = {
        "en": f"""
        <p>Dear {student_name},</p>
        <p>Please find your latest grade report attached.</p>
        <p>Best regards,<br>Grade System Admin</p>
        """,
        "zh": f"""
        <p>{student_name} 您好，</p>
        <p>請查收您的最新成績單（如附件）。</p>
        <p>祝好，<br>成績查詢系統管理員</p>
        """
    }

    message = MessageSchema(
        subject=subject_map.get(lang, subject_map["en"]),
        recipients=[student_email],
        body=body_map.get(lang, body_map["en"]),
        subtype=MessageType.html,
        attachments=[
            {
                "file": pdf_bytes,
                "filename": f"Grade_Report_{student_name}.pdf",
                "mime_type": "application/pdf",
                "headers": {}
            }
        ]
    )

    try:
        await fastmail.send_message(message)
        print(f"Email sent to {student_email}")
        return True
    except Exception as e:
        print(f"Failed to send email to {student_email}: {e}")
        return False
