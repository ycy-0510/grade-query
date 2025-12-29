import os
import base64
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.services.pdf import generate_student_pdf_bytes
from app.crud import calculate_student_grades
from sqlmodel import Session
import logging

logger = logging.getLogger("uvicorn")

async def send_email_task(
    user_creds: dict,
    student_id: int,
    subject: str,
    body_template: str,
    sender_name: str,
    session: Session
):
    """
    Sends a single email to a student with their grade PDF.
    This is meant to be run in a loop/queue.
    """
    from app.models import User

    student = session.get(User, student_id)
    if not student or not student.email:
        return {"student_id": student_id, "status": "failed", "error": "Student not found or no email"}

    try:
        # Fetch data in main thread (thread-safe for session)
        report = calculate_student_grades(student_id, session)
        if not report:
             return {"student_id": student_id, "name": student.name, "status": "failed", "error": "No grade data"}

        # Generate PDF in thread to avoid blocking loop
        # We pass the report object, not the session, to ensure thread safety
        pdf_bytes = await asyncio.to_thread(generate_student_pdf_bytes, report)

        # Build Email
        message = MIMEMultipart()
        message['to'] = student.email
        message['subject'] = subject

        # Ensure sender name is respected
        # Gmail API uses the authenticated user's email, but 'from' header can set display name
        message['from'] = sender_name

        # Body
        # Simple template replacement
        body = body_template.replace("{{name}}", student.name)
        message.attach(MIMEText(body, 'plain'))

        # Attachment
        part = MIMEApplication(pdf_bytes, Name=f"Grades_{student.seat_number}.pdf")
        part['Content-Disposition'] = f'attachment; filename="Grades_{student.seat_number}.pdf"'
        message.attach(part)

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

        # Google API Client
        # Reconstruct Credentials object
        creds = Credentials(
            token=user_creds.get('access_token'),
            refresh_token=user_creds.get('refresh_token'),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get('GOOGLE_CLIENT_ID'),
            client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        )

        def _send():
            service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
            return service.users().messages().send(userId='me', body={'raw': raw_message}).execute()

        await asyncio.to_thread(_send)

        return {"student_id": student_id, "name": student.name, "status": "sent"}

    except Exception as e:
        logger.error(f"Failed to send email to {student.name}: {e}")
        return {"student_id": student_id, "name": student.name, "status": "failed", "error": str(e)}

async def send_bulk_emails(
    student_ids: list[int],
    subject: str,
    body: str,
    user_creds: dict,
    sender_name: str,
    session_factory
):
    """
    Generator that yields status updates.
    """
    total = len(student_ids)
    processed = 0

    # Rate limit: 5 per second = 0.2s interval.

    for sid in student_ids:
        start_time = asyncio.get_event_loop().time()

        # Create a fresh DB session for each task or batch
        # Using session_factory ensures we don't reuse sessions across async boundaries in ways that might break (e.g. SQLite)

        with session_factory() as session:
            result = await send_email_task(user_creds, sid, subject, body, sender_name, session)

        yield result
        processed += 1

        # Rate Limiting
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed < 0.2:
            await asyncio.sleep(0.2 - elapsed)
