from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from typing import List, Optional
from datetime import datetime, timedelta
import json

from app.core.database import get_session
from app.models import User, ExamType, Score, UserRole, SubmissionLog, SubmissionStatus
from app.dependencies import is_admin, csrf_protect
from app.crud import (
    process_excel_upload, process_student_upload, get_score_matrix, bulk_update_scores,
    export_db_to_json, import_db_from_json, generate_grades_excel, delete_exam,
    toggle_exam_submission, get_submission_logs, get_all_exams, get_login_logs,
    update_exam_deadline, is_exam_effectively_open
)
from app.core.i18n import TRANSLATIONS
from pydantic import BaseModel

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals['translations'] = TRANSLATIONS

# --- Admin Routes ---
@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        return RedirectResponse("/")

    exams = get_all_exams(session)
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "exams": exams,
        "lang": lang,
        "now_utc": datetime.utcnow()
    })

@router.post("/admin/upload-grades", dependencies=[Depends(csrf_protect)])
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

@router.post("/admin/exams/create", dependencies=[Depends(csrf_protect)])
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

@router.post("/admin/exams/delete", dependencies=[Depends(csrf_protect)])
async def delete_exam_route(request: Request, exam_id: int = Form(...), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    delete_exam(session, exam_id)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/exams/toggle-submission", dependencies=[Depends(csrf_protect)])
async def toggle_submission_route(request: Request, exam_id: int = Form(...), is_open: bool = Form(False), session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    toggle_exam_submission(session, exam_id, is_open)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/exams/deadline", dependencies=[Depends(csrf_protect)])
async def update_exam_deadline_route(
    request: Request,
    exam_id: int = Form(...),
    deadline_str: Optional[str] = Form(None),
    timezone_offset: int = Form(0), # Minutes: UTC - Local (e.g. Beijing is -480)
    session: Session = Depends(get_session)
):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    deadline_dt = None
    if deadline_str and deadline_str.strip():
        try:
            # Parse naive datetime from input (e.g. "2023-10-27T10:00")
            naive_dt = datetime.fromisoformat(deadline_str)

            utc_dt = naive_dt + timedelta(minutes=timezone_offset)
            deadline_dt = utc_dt
        except ValueError:
            pass # Handle invalid format?

    update_exam_deadline(session, exam_id, deadline_dt)
    return RedirectResponse(url="/admin", status_code=303)

class ExamStatusUpdate(BaseModel):
    exam_id: int
    is_open: bool
    deadline: Optional[str] = None
    timezone_offset: int = 0

@router.post("/admin/api/exams/update-status", dependencies=[Depends(csrf_protect)])
async def update_exam_status_api(
    request: Request,
    data: ExamStatusUpdate,
    session: Session = Depends(get_session)
):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    exam = session.get(ExamType, data.exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # Update Open Status
    exam.is_open_for_submission = data.is_open

    # Update Deadline
    deadline_dt = None
    if data.deadline and data.deadline.strip():
        try:
            # Parse naive datetime (e.g. "2023-10-27T10:00")
            naive_dt = datetime.fromisoformat(data.deadline)
            # Apply offset to get UTC
            utc_dt = naive_dt + timedelta(minutes=data.timezone_offset)
            deadline_dt = utc_dt
        except ValueError:
            pass # Ignore invalid format

    exam.submission_deadline = deadline_dt
    session.add(exam)
    session.commit()
    session.refresh(exam)

    return JSONResponse({
        "success": True,
        "exam_id": exam.id,
        "is_open": exam.is_open_for_submission,
        "deadline": exam.submission_deadline.isoformat() if exam.submission_deadline else None,
        "is_effectively_open": is_exam_effectively_open(exam)
    })

@router.post("/admin/update-exams", dependencies=[Depends(csrf_protect)])
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

@router.get("/admin/scores", response_class=HTMLResponse)
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

@router.post("/admin/scores/update", dependencies=[Depends(csrf_protect)])
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

@router.post("/admin/upload-students", dependencies=[Depends(csrf_protect)])
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

@router.get("/admin/export-json")
async def export_json(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    data = export_db_to_json(session)
    return JSONResponse(content=data, headers={"Content-Disposition": "attachment; filename=database.json"})

@router.post("/admin/import-json", dependencies=[Depends(csrf_protect)])
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

@router.get("/admin/export-grades-excel")
async def export_grades_excel(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")

    output = generate_grades_excel(session)

    headers = {
        'Content-Disposition': 'attachment; filename="student_grades.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@router.get("/admin/logs", response_class=HTMLResponse)
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

@router.get("/admin/login-logs", response_class=HTMLResponse)
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
