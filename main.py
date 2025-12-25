from fastapi import FastAPI, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from typing import List, Optional
import os
import json
import httpx
from google import genai
from google.genai import types
from PIL import Image
import io

from database import init_db, get_session
from models import User, Score, ExamType, UserRole, SubmissionLog, SubmissionStatus
from sqlmodel import Session, select
from auth import oauth
from crud import (
    process_excel_upload, calculate_student_grades, create_user, get_user_by_email, 
    process_student_upload, get_score_matrix, bulk_update_scores, export_db_to_json, 
    import_db_from_json, generate_grades_excel, delete_exam, toggle_exam_submission,
    create_submission_log, get_student_submission_status, get_submission_logs, get_all_exams,
    create_login_log, cleanup_old_login_logs, get_login_logs
)
from i18n import TRANSLATIONS

app = FastAPI()
SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe_dev_secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")
templates.env.globals['translations'] = TRANSLATIONS

# --- AI Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY")

@app.on_event("startup")
def on_startup():
    init_db()
    # Cleanup old logs (3 days retention)
    from database import engine
    from sqlmodel import Session
    with Session(engine) as session:
        cleanup_old_login_logs(session, retention_days=3)

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

@app.get("/set-language/{lang}")
async def set_language(lang: str, request: Request):
    if lang not in ["en", "zh"]:
        lang = "en"
    response = RedirectResponse(url=request.headers.get("referer", "/"))
    response.set_cookie(key="lang", value=lang, max_age=31536000) # 1 year
    return response

# --- Auth Routes ---
@app.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for('auth_google')
    # Conditionally force HTTPS if running behind a proxy handling SSL
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        redirect_uri = str(redirect_uri).replace("http://", "https://")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/google")
async def auth_google(request: Request, session: Session = Depends(get_session)):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
            # Try to fetch from userinfo endpoint if not in token
            user_info = await oauth.google.userinfo(token=token)
    except Exception as e:
        return HTMLResponse(f"Auth failed: {str(e)}", status_code=400)
    
    email = user_info['email']
    name = user_info['name']
    
    # Check User
    db_user = get_user_by_email(session, email)
    
    # Initial Admin Seeding (Quick Hack)
    initial_admin = os.environ.get("INITIAL_ADMIN_EMAIL")
    if not db_user and initial_admin and email == initial_admin:
        # Auto-create admin
        db_user = User(email=email, name=name, role=UserRole.ADMIN)
        db_user = create_user(session, db_user)
    
    if not db_user:
        return HTMLResponse("<h1>Unauthorized</h1><p>Your email is not in the system.</p>", status_code=401)
    
    # Create Session
    request.session['user'] = {
        'id': db_user.id,
        'email': db_user.email,
        'name': db_user.name,
        'role': db_user.role,
        'seat_number': db_user.seat_number
    }
    
    
    if db_user.role == UserRole.ADMIN:
        # Create Login Log
        create_login_log(
            session, 
            email=db_user.email, 
            role=db_user.role, 
            ip_address=get_real_ip(request), 
            user_id=db_user.id, 
            name=db_user.name,
            user_agent=request.headers.get("user-agent")
        )
        return RedirectResponse(url='/admin')
    else:
        # Create Login Log
        create_login_log(
            session, 
            email=db_user.email, 
            role=db_user.role, 
            ip_address=get_real_ip(request), 
            user_id=db_user.id, 
            name=db_user.name,
            user_agent=request.headers.get("user-agent")
        )
        return RedirectResponse(url='/student')

@app.get("/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')

# --- Main Routes ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = request.session.get('user')
    if user:
        if user['role'] == 'admin':
            return RedirectResponse(url='/admin')
        else:
            return RedirectResponse(url='/student')
            return RedirectResponse(url='/student')
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("login.html", {"request": request, "lang": lang})

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("privacy_policy.html", {"request": request, "lang": lang})

@app.get("/tos", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("terms_of_service.html", {"request": request, "lang": lang})

# --- Admin Routes ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")
    
    exams = get_all_exams(session)
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("admin.html", {"request": request, "exams": exams, "lang": lang})

@app.post("/admin/upload-grades")
async def upload_grades(request: Request, files: List[UploadFile] = File(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    total_stats = {"created_exams": 0, "processed_scores": 0, "errors": 0}
    
    for file in files:
        stats = process_excel_upload(file.file, session)
        for k, v in stats.items():
            if k in total_stats:
                total_stats[k] += v
            else:
                total_stats[k] = v
    
    exams = get_all_exams(session)
    
    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "exams": exams, 
        "error": f"Processed {len(files)} files! Total Stats: {total_stats}",
        "lang": request.cookies.get("lang", "en")
    }) 

@app.post("/admin/exams/create")
async def create_exam_manual(request: Request, exam_name: str = Form(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    exam_name = exam_name.strip()
    if not exam_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
        
    existing = session.exec(select(ExamType).where(ExamType.name == exam_name)).first()
    if not existing:
        new_exam = ExamType(name=exam_name)
        session.add(new_exam)
        session.commit()
    
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/exams/delete")
async def delete_exam_route(request: Request, exam_id: int = Form(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    delete_exam(session, exam_id)
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/exams/toggle-submission")
async def toggle_submission_route(request: Request, exam_id: int = Form(...), is_open: bool = Form(False), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    toggle_exam_submission(session, exam_id, is_open)
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/update-exams")
async def update_exams_config(
        request: Request, 
        mandatory_exams: List[int] = Form(default=[]), 
        session: Session = Depends(get_session)
    ):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    # Reset all to False first
    all_exams = get_all_exams(session)
    for exam in all_exams:
        exam.is_mandatory = False
        session.add(exam)
    
    # Set selected to True
    for exam_id in mandatory_exams:
        exam = session.get(ExamType, exam_id)
        if exam:
            exam.is_mandatory = True
            session.add(exam)
            
    session.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin/scores", response_class=HTMLResponse)
async def view_scores(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")
        
    students, exams, score_map = get_score_matrix(session)
    
    return templates.TemplateResponse("admin_scores.html", {
        "request": request,
        "students": students,
        "exams": exams,
        "score_map": score_map,
        "lang": request.cookies.get("lang", "en")
    })

@app.post("/admin/scores/update")
async def update_scores(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    form_data = await request.form()
    # Convert form data to dict for processing
    # Note: request.form() returns generic FormData, need to iterate
    data = {k: v for k, v in form_data.items()}
    
    count = bulk_update_scores(data, session)
    
    # Redirect back to view
    return RedirectResponse(url="/admin/scores", status_code=303)

@app.post("/admin/upload-students")
async def upload_students(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    stats = process_student_upload(file.file, session)
    
    exams = get_all_exams(session)
    
    msg = f"Students Processed: {stats}"
    if "error" in stats:
        msg = f"Error: {stats['error']}"

    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "exams": exams, 
        "error": msg,
        "lang": request.cookies.get("lang", "en")
    })

@app.get("/admin/export-json")
async def export_json(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    data = export_db_to_json(session)
    return JSONResponse(content=data, headers={"Content-Disposition": "attachment; filename=database.json"})

@app.post("/admin/import-json")
async def import_json(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    try:
        content = await file.read()
        data = json.loads(content)
        stats = import_db_from_json(data, session)
        msg = f"Import Successful: {stats}"
    except Exception as e:
        msg = f"Import Failed: {str(e)}"
        
    exams = get_all_exams(session)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "exams": exams,
        "error": msg,
        "lang": request.cookies.get("lang", "en")
    })

@app.get("/admin/export-grades-excel")
async def export_grades_excel(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    output = generate_grades_excel(session)
    
    headers = {
        'Content-Disposition': 'attachment; filename="student_grades.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")
        
    logs = get_submission_logs(session)
    
    return templates.TemplateResponse("admin_logs.html", {
        "request": request,
        "logs": logs,
        "lang": request.cookies.get("lang", "en")
    })

@app.get("/admin/login-logs", response_class=HTMLResponse)
async def admin_login_logs(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")
        
    logs = get_login_logs(session)
    
    return templates.TemplateResponse("admin_login_logs.html", {
        "request": request,
        "logs": logs,
        "lang": request.cookies.get("lang", "en")
    })

# --- Student Routes ---
@app.get("/student", response_class=HTMLResponse)
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
                open_submissions.append({
                    "id": exam.id,
                    "name": exam.name,
                    "attempt_count": count,
                    "status": status,
                    "can_submit": count < 3 and status != SubmissionStatus.APPROVED
                })

    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("student.html", {
        "request": request, 
        "report": report,
        "open_submissions": open_submissions,
        "lang": lang
    })

@app.get("/student/logs", response_class=HTMLResponse)
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

@app.get("/student/submit/{exam_id}", response_class=HTMLResponse)
async def student_submit_page(request: Request, exam_id: int, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'student':
        return RedirectResponse("/")
        
    exam = session.get(ExamType, exam_id)
    if not exam or not exam.is_open_for_submission:
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

@app.post("/student/submit/{exam_id}")
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
