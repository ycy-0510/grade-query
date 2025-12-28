from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session
from typing import Optional
from datetime import datetime
import json
import os
import httpx
from google import genai
from google.genai import types

from app.core.database import get_session
from app.models import ExamType, Score, SubmissionLog, SubmissionStatus
from app.dependencies import csrf_protect, get_current_user
from app.crud import (
    calculate_student_grades, create_submission_log, get_student_submission_status,
    get_submission_logs, get_all_exams, is_exam_effectively_open
)
from app.core.i18n import TRANSLATIONS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals['translations'] = TRANSLATIONS

# --- AI Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Failed to init Gemini: {e}")

TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY")

def get_real_ip(request: Request) -> str:
    # Cloudflare Tunnel / Proxy Real IP Support
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]
    return request.client.host

async def verify_turnstile(token: str, ip: str) -> bool:
    if not TURNSTILE_SECRET_KEY:
        return True # Bypass if not configured

    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    data = {
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
        "remoteip": ip
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data)
        result = response.json()
        return result.get("success", False)

@router.get("/student", response_class=HTMLResponse)
async def student_dashboard(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'student':
        return RedirectResponse("/")

    report = calculate_student_grades(user['id'], session)

    # Check open submissions
    open_submissions = []
    # exams = session.exec(select(ExamType).where(ExamType.is_open_for_submission == True)).all() # type mismatch possible
    # Just select all and filter
    all_exams = get_all_exams(session)

    for exam in all_exams:
        if exam.is_open_for_submission:
            # Check if grade exists
            has_grade = False
            for detail in report['details']:
                if detail['exam_name'] == exam.name:
                    # If score is present, we consider it done.
                    if detail['score'] is not None:
                        has_grade = True
                    break

            if not has_grade:
                # Get status info
                count, status = get_student_submission_status(session, user['id'], exam.id)

                # Check Open Status
                if not is_exam_effectively_open(exam):
                    # Treat as not open (hidden)
                    continue

                open_submissions.append({
                    "id": exam.id,
                    "name": exam.name,
                    "attempt_count": count,
                    "status": status,
                    "can_submit": count < 3 and status != SubmissionStatus.APPROVED,
                    "submission_deadline": exam.submission_deadline
                })

    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("student.html", {
        "request": request,
        "report": report,
        "open_submissions": open_submissions,
        "lang": lang
    })

@router.get("/student/logs", response_class=HTMLResponse)
async def student_logs(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'student':
        return RedirectResponse("/")

    logs = get_submission_logs(session, user_id=user['id'])

    return templates.TemplateResponse("student_logs.html", {
        "request": request,
        "logs": logs,
        "lang": request.cookies.get("lang", "en")
    })

@router.get("/student/submit/{exam_id}", response_class=HTMLResponse)
async def student_submit_page(request: Request, exam_id: int, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'student':
        return RedirectResponse("/")

    exam = session.get(ExamType, exam_id)
    if not exam or not is_exam_effectively_open(exam):
        return RedirectResponse("/student")

    count, status = get_student_submission_status(session, user['id'], exam_id)

    if count >= 3 or status == SubmissionStatus.APPROVED:
        # Already done or failed
        return RedirectResponse("/student")

    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("submission.html", {
        "request": request,
        "exam": exam,
        "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY"),
        "attempts_left": 3 - count,
        "lang": lang
    })

@router.post("/student/submit/{exam_id}", dependencies=[Depends(csrf_protect)])
async def student_submit_action(
    request: Request,
    exam_id: int,
    score_claim: float = Form(...),
    image: UploadFile = File(...),
    cf_turnstile_response: str = Form(alias="cf-turnstile-response"),
    session: Session = Depends(get_session)
):
    try:
        user = request.session.get('user')
        if not user or user['role'] != 'student':
            raise HTTPException(status_code=403, detail="Unauthorized")

        # 1. Verify Turnstile
        client_ip = get_real_ip(request)
        if not await verify_turnstile(cf_turnstile_response, client_ip):
             return JSONResponse({"success": False, "error": "CAPTCHA Verification Failed"}, status_code=400)

        # 2. Check Attempts
        count, status = get_student_submission_status(session, user['id'], exam_id)
        if count >= 3:
             return JSONResponse({"success": False, "error": "Max attempts reached"}, status_code=400)

        if status == SubmissionStatus.APPROVED:
             return JSONResponse({"success": True, "message": "Already approved"}, status_code=200)

        # 3. AI Verification
        exam = session.get(ExamType, exam_id)
        if not exam:
            return JSONResponse({"success": False, "error": "Exam not found"}, status_code=404)

        # Check Deadline/Open Status
        if not is_exam_effectively_open(exam):
             return JSONResponse({"success": False, "error": "Submission is closed"}, status_code=400)

        # Read image
        content = await image.read()
        mime_type = image.content_type or "image/jpeg"

        # Prompt Construction

        if not client:
             return JSONResponse({"success": False, "error": "AI not configured"}, status_code=500)

        # 1. System Instruction (Static Rules & Persona)
        sys_instruct = """You are a diligent and thorough teacher assistant verifying a student's exam paper upload.
Your goal is to find the score on the paper, even if it is handwritten, small, or in a corner.

Please analyze the image CAREFULLY and return a JSON object with these fields:
- "detected_exam_name": string (name of exam found on paper, or null)
- "detected_score": number (final score found on paper, or null)
- "is_clear": boolean (is the image clear and legible?)
- "is_complete": boolean (does it look like a full exam paper?)
- "confidence": number (0-100, how confident are you that this is the correct exam with the claimed score?)
- "reason": string (explanation of your decision)

Instructions:
1. Look for the score EVERYWHERE on the page (top corners, bottom, margins).
2. Look for handwritten numbers, red ink, or circled numbers that indicate a total score.
3. Even if the exam name is slightly different (e.g. abbreviation), if it looks correct, accept it.
4. If the score is not clearly labeled "Total", infer it from the largest circled number or the sum of marks.

Rules for high confidence (>75):
1. Image must be relatively clear.
2. You found a score that matches the Student Claimed Score.
3. The document looks like the correct exam."""

        # 2. User Message (Dynamic Context)
        user_content = f"""Expected Exam Name: "{exam.name}"
Student Claimed Score: {score_claim}

Please verify if the image matches these details."""

        # Use a model that supports vision
        # Using gemini-2.5-flash as requested, WITH system instructions via types.GenerateContentConfig

        # Pass dictionary with mime_type and data directly
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=user_content),
                        types.Part.from_bytes(data=content, mime_type=mime_type)
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=sys_instruct
            )
        )
        text_response = response.text

        # Clean response to get JSON
        json_str = text_response.replace("```json", "").replace("```", "").strip()
        try:
            ai_data = json.loads(json_str)
        except json.JSONDecodeError:
            return JSONResponse({"success": False, "error": "AI response was not valid JSON"}, status_code=500)

        confidence = ai_data.get("confidence", 0)

        # 4. Process Logic
        new_status = SubmissionStatus.REJECTED
        error_msg = ai_data.get("reason", "Unknown error")

        if confidence > 75:
            new_status = SubmissionStatus.APPROVED
            # Save Score
            new_score = Score(user_id=user['id'], exam_type_id=exam.id, score=score_claim)
            session.add(new_score)

        # Create Log
        log = SubmissionLog(
            user_id=user['id'],
            exam_type_id=exam.id,
            attempt_count=count + 1,
            status=new_status,
            ai_response_json=json.dumps(ai_data)
        )
        create_submission_log(session, log)

        if new_status == SubmissionStatus.APPROVED:
             return JSONResponse({"success": True, "redirect": "/student"})
        else:
             # Calculate remaining
             attempts_used = count + 1
             remaining = max(0, 3 - attempts_used)

             return JSONResponse({
                 "success": False,
                 "error": "Verification Failed",
                 "remaining_attempts": remaining,
                 "detail": {
                     "reason": error_msg,
                     "confidence": confidence,
                     "message": f"Our AI could not verify this submission. Reason: {error_msg}"
                 }
             }, status_code=400)

    except Exception as e:
        import traceback
        traceback.print_exc() # Print to console for Docker logs
        return JSONResponse({"success": False, "error": f"Server Error: {str(e)}"}, status_code=500)
