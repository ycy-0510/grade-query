import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from googleapiclient.discovery import build
from datetime import datetime

from i18n import TRANSLATIONS

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

def send_grade_email_gmail(
    creds,
    student_email: str,
    student_name: str,
    pdf_bytes: bytes,
    lang: str = "en"
):
    """
    Sends an email with the grade report PDF attached using Gmail API.
    Blocking function, should be run in a thread.
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

    try:
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)

        message = MIMEMultipart()
        message['to'] = student_email
        message['subject'] = subject_map.get(lang, subject_map["en"])

        html_part = MIMEText(body_map.get(lang, body_map["en"]), 'html')
        message.attach(html_part)

        # Attachment
        part = MIMEApplication(pdf_bytes, Name=f"Grade_Report_{student_name}.pdf")
        part['Content-Disposition'] = f'attachment; filename="Grade_Report_{student_name}.pdf"'
        message.attach(part)

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

        service.users().messages().send(userId='me', body={'raw': raw_message}).execute()
        print(f"Email sent to {student_email} via Gmail API")
        return True
    except Exception as e:
        print(f"Failed to send email to {student_email} via Gmail API: {e}")
        return False
