from fastapi import FastAPI, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from typing import List, Optional
import os
import json

from database import init_db, get_session
from models import User, Score, ExamType, UserRole
from sqlmodel import Session, select
from auth import oauth
from crud import process_excel_upload, calculate_student_grades, create_user, get_user_by_email, process_student_upload, get_score_matrix, bulk_update_scores, export_db_to_json, import_db_from_json, generate_grades_excel








app = FastAPI()
SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe_dev_secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
def on_startup():
    init_db()

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
        return RedirectResponse(url='/admin')
    else:
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
    return templates.TemplateResponse("login.html", {"request": request})

# --- Admin Routes ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")
    
    exams = session.exec(select(ExamType)).all()
    return templates.TemplateResponse("admin.html", {"request": request, "exams": exams})

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
    
    exams = session.exec(select(ExamType)).all()
    
    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "exams": exams, 
        "error": f"Processed {len(files)} files! Total Stats: {total_stats}" 
    }) 

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
    all_exams = session.exec(select(ExamType)).all()
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
        "score_map": score_map
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
    
    exams = session.exec(select(ExamType)).all()
    
    msg = f"Students Processed: {stats}"
    if "error" in stats:
        msg = f"Error: {stats['error']}"

    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "exams": exams, 
        "error": msg 
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
        
    exams = session.exec(select(ExamType)).all()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "exams": exams,
        "error": msg
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

# --- Student Routes ---
@app.get("/student", response_class=HTMLResponse)
async def student_dashboard(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'student':
        return RedirectResponse("/")
    
    report = calculate_student_grades(user['id'], session)
    return templates.TemplateResponse("student.html", {"request": request, "report": report})
