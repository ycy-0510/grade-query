from fastapi import APIRouter, Depends, Request, HTTPException, Body
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session, select
from typing import List, Dict
import json
import os
import asyncio
from app.core.database import get_session, engine
from app.models import User, UserRole
from app.dependencies import is_admin, csrf_protect
from app.services.email import send_bulk_emails

router = APIRouter()

@router.get("/admin/api/students")
async def get_students_for_email(request: Request, session: Session = Depends(get_session), user: dict = Depends(is_admin)):
    """
    Returns list of students for email selection.
    """
    students = session.exec(select(User).where(User.role == UserRole.STUDENT)).all()
    # Sort by seat number
    def try_int(s):
        try:
            return int(s)
        except:
            return 999999

    students.sort(key=lambda u: try_int(u.seat_number))

    data = [
        {"id": s.id, "name": s.name, "email": s.email, "seat_number": s.seat_number}
        for s in students if s.email # Only those with email
    ]
    return JSONResponse(data)

@router.post("/admin/api/send-grades", dependencies=[Depends(csrf_protect)])
async def send_grades_api(
    request: Request,
    payload: Dict = Body(...),
    user: dict = Depends(is_admin)
):
    """
    Streaming response for sending emails.
    Payload: { student_ids: [], subject: str, body: str }
    """
    student_ids = payload.get("student_ids", [])
    subject = payload.get("subject", "")
    body = payload.get("body", "")

    if not student_ids:
        return JSONResponse({"error": "No students selected"}, status_code=400)

    # Check Auth
    gmail_token = request.session.get('gmail_token')
    if not gmail_token:
        # 403 with specific code to trigger auth flow on frontend
        return JSONResponse({"error": "auth_required"}, status_code=403)

    sender_name = os.environ.get("EMAIL_SENDER_NAME", "Grade System Admin")

    # Define generator
    async def event_generator():
        # Session Factory for async generator (to create new session per task)
        # We can use the engine directly to create sessions.
        session_factory = lambda: Session(engine)

        async for result in send_bulk_emails(student_ids, subject, body, gmail_token, sender_name, session_factory):
            # SSE Format: data: json_string\n\n
            yield f"data: {json.dumps(result)}\n\n"

        yield "event: close\ndata: close\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
