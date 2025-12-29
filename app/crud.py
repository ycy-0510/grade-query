from sqlmodel import Session, select, delete
from app.models import User, ExamType, Score, UserRole, SubmissionLog, SubmissionStatus, LoginLog
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional
import json
from datetime import datetime

def delete_exam(session: Session, exam_id: int):
    # Delete associated scores and submissions first (cascade usually handles this if configured, but let's be safe)
    session.exec(delete(Score).where(Score.exam_type_id == exam_id))
    session.exec(delete(SubmissionLog).where(SubmissionLog.exam_type_id == exam_id))
    
    exam = session.get(ExamType, exam_id)
    if exam:
        session.delete(exam)
        session.commit()

def toggle_exam_submission(session: Session, exam_id: int, is_open: bool):
    exam = session.get(ExamType, exam_id)
    if exam:
        exam.is_open_for_submission = is_open
        session.add(exam)
        session.add(exam)
        session.commit()

def is_exam_effectively_open(exam: ExamType) -> bool:
    """
    Determines if an exam is effectively open for submission.
    Requires:
    1. Manual switch (is_open_for_submission) is True
    2. Deadline is either None or in the future
    """
    if not exam.is_open_for_submission:
        return False
    if exam.submission_deadline and datetime.utcnow() > exam.submission_deadline:
        return False
    return True


def update_exam_deadline(session: Session, exam_id: int, deadline: Optional[datetime]):
    exam = session.get(ExamType, exam_id)
    if exam:
        exam.submission_deadline = deadline
        session.add(exam)
        session.commit()

def create_submission_log(session: Session, log: SubmissionLog):
    session.add(log)
    session.commit()
    session.refresh(log)
    return log

def get_student_submission_status(session: Session, user_id: int, exam_id: int):
    """
    Returns (attempt_count, current_status)
    """
    submissions = session.exec(select(SubmissionLog).where(
        SubmissionLog.user_id == user_id, 
        SubmissionLog.exam_type_id == exam_id
    ).order_by(SubmissionLog.last_attempt_time.desc())).all()
    
    # Calculate total attempts (count of logs? or one log object per exam?)
    # Plan says: "Track attempts... Fields: attempt_count". 
    # Let's assume one log record PER attempt if we want history, or one record per user-exam pair updated.
    # The models.py definition allows multiple logs. Let's append logs. 
    # Actually, simpler model: One row per attempt. 
    # Or strict count: Query count of rows.
    
    # BUT wait, the plan implies we want to enforce 3 attempts.
    # Let's count how many logs exist for this user+exam.
    
    count = len(submissions)
    
    
    # Check if any is approved
    is_approved = any(s.status == SubmissionStatus.APPROVED for s in submissions)
    status = SubmissionStatus.APPROVED if is_approved else SubmissionStatus.PENDING
    if count > 0 and not is_approved:
        # Determine strict status from last log
        status = submissions[0].status
        
    # Check Deadline
    exam = session.get(ExamType, exam_id)
    if exam and exam.submission_deadline and datetime.utcnow() > exam.submission_deadline:
        # If deadline passed, we can mark as effectively closed/rejected if not approved
        # But we don't change the DB status, just return a "Closed" indicator?
        # The function returns (count, status). 
        # The CALLER checks if it can submit.
        # So we don't need to change return value, but we might want to check this in 'can_submit' logic elsewhere.
        pass

    return count, status

def get_user_by_email(session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    return session.exec(statement).first()

def create_user(session: Session, user: User) -> User:
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

def process_excel_upload(file_obj, session: Session) -> Dict[str, int]:
    """
    Parses the Excel file and updates database.
    Assumes Column 1 is 'seat_number', then Exam Names.
    """
    from io import BytesIO
    # Fix for SpooledTemporaryFile seekable error
    content = file_obj.read()
    df = pd.read_excel(BytesIO(content))
    
    # Clean column names
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]
    
    # 1. Identify Exam Columns (Exclude seat_number/Student Name/etc if any, identifying strict columns starting from 2nd)
    # Spec says: Col 1: seat_number, Col 2+: Exam Names.
    # Let's trust spec strictly. df.iloc[:, 0] is seat_number. df.iloc[:, 1:] are exams.
    
    exam_columns = df.columns[1:]
    stats = {"created_exams": 0, "processed_scores": 0, "errors": 0}
    
    # 2. Ensure Exams Exist
    exam_name_to_id = {}
    for exam_name in exam_columns:
        # Check if exists
        statement = select(ExamType).where(ExamType.name == exam_name)
        exam_type = session.exec(statement).first()
        
        if not exam_type:
            exam_type = ExamType(name=exam_name, is_mandatory=False)
            session.add(exam_type)
            session.commit()
            session.refresh(exam_type)
            stats["created_exams"] += 1
        
        exam_name_to_id[exam_name] = exam_type.id
    
    # 3. Iterate Rows
    for index, row in df.iterrows():
        raw_val = row.iloc[0]
        seat_num = str(raw_val).strip()
        # Handle cases where pandas reads integers as floats (e.g., "1.0" -> "1")
        if seat_num.endswith(".0"):
            seat_num = seat_num[:-2]
        
        # Find user by seat_number
        statement = select(User).where(User.seat_number == seat_num)
        user = session.exec(statement).first()
        
        if not user:
            # Skip if user doesn't exist? Or maybe create placeholder? 
            # Spec says: "Mapping: Match Row's seat_number to User.id" (actually seat number field).
            # If user not found, we can't add scores.
            print(f"User with seat number {seat_num} not found. Skipping.")
            stats["errors"] += 1
            continue
            
        for exam_name in exam_columns:
            val = row[exam_name]
            
            # Validation: Numeric check
            try:
                if pd.isna(val):
                    continue
                score_val = float(val)
            except (ValueError, TypeError):
                # "Abs", "N/A", etc.
                continue
            
            # Update/Insert Score
            # Check if score exists for this user + exam
            exam_id = exam_name_to_id[exam_name]
            score_stmt = select(Score).where(Score.user_id == user.id, Score.exam_type_id == exam_id)
            existing_score = session.exec(score_stmt).first()
            
            if existing_score:
                existing_score.score = score_val
                session.add(existing_score)
            else:
                new_score = Score(user_id=user.id, exam_type_id=exam_id, score=score_val)
                session.add(new_score)
            
            stats["processed_scores"] += 1
            
    session.commit()
    return stats

def calculate_student_grades(
    user_id: int,
    session: Session,
    user: Optional[User] = None,
    all_exams: Optional[List[ExamType]] = None,
    user_scores: Optional[List[Score]] = None,
    include_submission_status: bool = True
) -> Dict[str, Any]:
    """
    Implements the Top 20 Rule.
    - Mandatory exams: Must be included. If missing, count as 0.
    - Optional exams: Only Top N used to fill up to 20 slots.
    """
    if user is None:
        user = session.get(User, user_id)
    if not user:
        return {}
        
    # 1. Fetch All Exams and User's Scores
    if all_exams is None:
        all_exams = get_all_exams(session)

    if user_scores is None:
        scores = session.exec(select(Score).where(Score.user_id == user_id)).all()
    else:
        scores = user_scores

    score_map = {s.exam_type_id: s.score for s in scores}
    
    mandatory_items = []
    optional_items = []
    
    formatted_rows = []
    
    for exam in all_exams:
        score_val = score_map.get(exam.id) # Float or None
        
        # Check submission eligibility
        can_submit = False
        if include_submission_status and is_exam_effectively_open(exam) and score_val is None:
             count, status = get_student_submission_status(session, user_id, exam.id)
             if count < 3 and status != SubmissionStatus.APPROVED:
                 can_submit = True

        item = {
            "exam_id": exam.id,
            "exam_name": exam.name,
            "score": score_val, # For Display (None becomes "-")
            "is_mandatory": exam.is_mandatory,
            "included": False,
            "can_submit": can_submit,
            "submission_deadline": exam.submission_deadline
        }
        
        formatted_rows.append(item)
        
        # Calculation Logic
        # If mandatory: Always included. If None -> 0.0
        if exam.is_mandatory:
            calc_val = score_val if score_val is not None else 0.0
            mandatory_items.append({
                "score": calc_val,
                "ref": item # Link back to update 'included'
            })
        else:
            # If optional: Only include if it has a score
            if score_val is not None:
                optional_items.append({
                    "score": score_val,
                    "ref": item
                })
        
    # Logic
    # 3. Select ALL Mandatory
    selected_scores = []
    for m_item in mandatory_items:
        m_item["ref"]["included"] = True
        selected_scores.append(m_item["score"])
        
    # 4. Calculate remaining slots
    slots_needed = 20 - len(mandatory_items)
    
    # 5. Select Top Slots from Optional
    if slots_needed > 0:
        # Sort optionals by score descending
        optional_items.sort(key=lambda x: x["score"], reverse=True)
        top_optionals = optional_items[:slots_needed]
        
        for o_item in top_optionals:
            o_item["ref"]["included"] = True
            selected_scores.append(o_item["score"])
            
    # Calculate Average
    effective_count = len(selected_scores)
    total_score = sum(selected_scores)
    
    # Divisor is max(20, effective_count) to handle cases where we have > 20 mandatory
    divisor = max(20, effective_count)
    
    if divisor == 0:
        average = 0.0
    else:
        average = total_score / divisor
        
    # Calculate valid exams count (> 0)
    valid_exam_count = 0
    for s in scores:
        if isinstance(s.score, (int, float)) and s.score > 0:
            valid_exam_count += 1

    return {
        "user_name": user.name,
        "seat_number": user.seat_number,
        "average": round(average, 2),
        "exam_count": len(selected_scores),
        "valid_exam_count": valid_exam_count,
        "details": formatted_rows
    }

def process_student_upload(file_obj, session: Session) -> Dict[str, int]:
    """
    Imports students from Excel.
    Expected Columns: 'seat_number', 'name', 'email'
    """
    from io import BytesIO
    # file_obj is SpooledTemporaryFile from FastAPI/Starlette.
    # Pandas/OpenPyXL needs seekable stream.
    # Read into memory first.
    content = file_obj.read()
    df = pd.read_excel(BytesIO(content))
    
    # Normalize headers
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]
    
    stats = {"created": 0, "updated": 0, "errors": 0}
    
    required_cols = ['seat_number', 'email', 'name']
    for col in required_cols:
        if col not in df.columns:
            # Fallback for no headers or different headers?
            # Let's try to map by index if headers don't match (0: seat, 1: name, 2: email)
            # But safer to just error or return message.
            # For simplicity, if columns missing, try index based:
            if len(df.columns) >= 3:
                # rename
                df.columns = ['seat_number', 'name', 'email'] + list(df.columns[3:])
                break
            else:
                return {"error": f"Missing columns. Need {required_cols} or at least 3 columns."}

    for index, row in df.iterrows():
        try:
            raw_seat = row['seat_number']
            seat_num = str(raw_seat).strip()
            if seat_num.endswith(".0"):
                seat_num = seat_num[:-2]
                
            email = str(row['email']).strip()
            name = str(row['name']).strip()
            
            # Check if user exists by email (unique key)
            statement = select(User).where(User.email == email)
            user = session.exec(statement).first()
            
            if user:
                user.seat_number = seat_num
                user.name = name
                # user.role = UserRole.STUDENT # Default
                session.add(user)
                stats["updated"] += 1
            else:
                user = User(email=email, name=name, seat_number=seat_num, role=UserRole.STUDENT)
                session.add(user)
                stats["created"] += 1
        except Exception as e:
            print(f"Error processing row {index}: {e}")
            stats["errors"] += 1
            
    session.commit()
    return stats

def get_score_matrix(session: Session):
    """
    Returns:
    - exams: List[ExamType]
    - students: List[User] (role=student)
    - score_map: Dict[(user_id, exam_id), float]
    """
    students = session.exec(select(User).where(User.role == UserRole.STUDENT)).all()
    
    def try_int(s):
        try:
            return int(s)
        except:
            return 999999
            
    students.sort(key=lambda u: try_int(u.seat_number))
    exams = get_all_exams(session)
    scores = session.exec(select(Score)).all()
    
    score_map = {}
    for s in scores:
        score_map[(s.user_id, s.exam_type_id)] = s.score
        
    return students, exams, score_map

def bulk_update_scores(form_data: dict, session: Session):
    """
    Parse form keys 'score_{user_id}_{exam_id}' and update.
    """
    updated_count = 0
    
    # Pre-fetch all scores to minimize queries? Or just upsert.
    # For simplicity, loop and upsert. Can optimize later.
    
    for key, value in form_data.items():
        if key.startswith("score_"):
            parts = key.split("_")
            if len(parts) == 3:
                user_id = int(parts[1])
                exam_id = int(parts[2])
                
                try:
                    if value == "":
                        continue # Ignore empty strings
                    score_val = float(value)
                except (ValueError, TypeError):
                    continue # Skip invalid inputs
                
                # Check exist
                stmt = select(Score).where(Score.user_id == user_id, Score.exam_type_id == exam_id)
                existing = session.exec(stmt).first()
                
                if existing:
                    if existing.score != score_val:
                        existing.score = score_val
                        session.add(existing)
                        updated_count += 1
                else:
                    new_sc = Score(user_id=user_id, exam_type_id=exam_id, score=score_val)
                    session.add(new_sc)
                    updated_count += 1
                    
    session.commit()
    return updated_count

def export_db_to_json(session: Session) -> Dict[str, Any]:
    """
    Exports all Users, ExamTypes, Scores, and SubmissionLogs to a dictionary.
    """
    users = session.exec(select(User)).all()
    exams = session.exec(select(ExamType)).all()
    scores = session.exec(select(Score)).all()
    logs = session.exec(select(SubmissionLog)).all()
    
    data = {
        "users": [u.model_dump(mode='json') for u in users],
        "exams": [e.model_dump(mode='json') for e in exams],
        "scores": [s.model_dump(mode='json') for s in scores],
        "logs": [l.model_dump(mode='json') for l in logs]
    }
    return data

def import_db_from_json(data: Dict[str, Any], session: Session) -> Dict[str, Any]:
    """
    Imports data from dictionary. WARNING: Deletes existing data.
    """
    stats = {"users": 0, "exams": 0, "scores": 0, "logs": 0, "errors": 0}
    
    try:
        # 1. Clear existing data
        # Order matters due to foreign keys: Log/Score -> (User, ExamType)
        session.exec(delete(SubmissionLog)) # SubmissionLog depends on User/Exam
        session.exec(delete(Score))         # Score depends on User/Exam
        session.exec(delete(User))
        session.exec(delete(ExamType))
        session.commit()
        
        # 2. Import Users
        users_data = data.get("users", [])
        for u_data in users_data:
            # Handle enum conversion if needed, but model_validate might handle it if string matches
            # 'id' should be preserved to maintain relationships if we preserve Score.id or references
            user = User.model_validate(u_data)
            session.add(user)
            stats["users"] += 1
            
        # 3. Import ExamTypes
        exams_data = data.get("exams", [])
        for e_data in exams_data:
            exam = ExamType.model_validate(e_data)
            session.add(exam)
            stats["exams"] += 1
            
        # Commit users and exams first so IDs exist for scores/logs
        # We need to ensure we insert with explicit IDs. 
        # SQLModel/SQLAlchemy usually allows inserting explicit IDs.
        session.commit()
        
        # 4. Import Scores
        scores_data = data.get("scores", [])
        for s_data in scores_data:
            score = Score.model_validate(s_data)
            session.add(score)
            stats["scores"] += 1
            
        # 5. Import Logs
        logs_data = data.get("logs", [])
        for l_data in logs_data:
            log = SubmissionLog.model_validate(l_data)
            session.add(log)
            stats["logs"] += 1

        session.commit()
        
    except Exception as e:
        session.rollback()
        stats["errors"] += 1
        stats["error_message"] = str(e)
        raise e
        
    return stats

def generate_grades_excel(session: Session):
    """
    Generates an Excel file with all student grades and their calculated Top 20 average.
    Returns BytesIO object.
    """
    from io import BytesIO
    
    # Get all students
    students = session.exec(select(User).where(User.role == UserRole.STUDENT)).all()
    students.sort(key=lambda u: int(u.seat_number) if u.seat_number and u.seat_number.isdigit() else 999999)
    
    # Get all exams for columns headers (sorted by Type/Name)
    all_exams = get_all_exams(session)
    
    # Pre-fetch all scores to avoid N+1 queries
    all_scores = session.exec(select(Score)).all()
    scores_by_user = {}
    for s in all_scores:
        if s.user_id not in scores_by_user:
            scores_by_user[s.user_id] = []
        scores_by_user[s.user_id].append(s)

    data = []
    
    for student in students:
        # Calculate grade report
        # Optimized to use pre-fetched data
        user_scores = scores_by_user.get(student.id, [])
        report = calculate_student_grades(
            student.id,
            session,
            user=student,
            all_exams=all_exams,
            user_scores=user_scores,
            include_submission_status=False
        )
        
        row = {
            "Seat Number": student.seat_number,
            "Name": student.name,
            "Top 20 Avg": report.get("average", 0.0)
        }
        
        # Map exam name to score
        score_map = {item['exam_name']: item['score'] for item in report.get('details', [])}
        
        for exam in all_exams:
            row[exam.name] = score_map.get(exam.name, None)
            
        data.append(row)
        
    df = pd.DataFrame(data)
    
    # Reorder columns: Seat, Name, Avg, ...Exams
    # Ensure all columns exist in df (in case data is empty)
    if not data:
        # Create empty df with columns
        cols = ["Seat Number", "Name", "Top 20 Avg"] + [e.name for e in all_exams]
        df = pd.DataFrame(columns=cols)
    else:
        cols = ["Seat Number", "Name", "Top 20 Avg"] + [e.name for e in all_exams]
        # Filter/Order columns. columns that might not exist in row if data was sparse? 
        # But we iterated all_exams to build row, so they exist.
        df = df[cols]
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Grades")
        
    output.seek(0)
    return output

def get_all_exams(session: Session) -> List[ExamType]:
    """
    Returns all exams sorted by:
    1. Type (Mandatory first -> is_mandatory=True)
    2. Name (Alphabetical)
    """
    return session.exec(select(ExamType).order_by(ExamType.is_mandatory.desc(), ExamType.name.asc())).all()

def get_submission_logs(session: Session, user_id: Optional[int] = None, limit: int = 100):
    """
    Fetches submission logs.
    If user_id is provided, filters by that user.
    """
    # Import here to avoid circulars if any, though likely fine at top. 
    # But safer since we are appending at end.
    
    query = select(SubmissionLog, User, ExamType).join(User).join(ExamType)
    
    if user_id:
        query = query.where(SubmissionLog.user_id == user_id)
        
    query = query.order_by(SubmissionLog.last_attempt_time.desc()).limit(limit)
    
    results = session.exec(query).all()
    
    logs = []
    for log, user, exam in results:
        # Parse JSON if possible to get clean reason
        reason = ""
        confidence = 0
        if log.ai_response_json:
            try:
                data = json.loads(log.ai_response_json)
                reason = data.get("reason", "")
                confidence = data.get("confidence", 0)
            except:
                pass
                
        logs.append({
            "id": log.id,
            "time": log.last_attempt_time,
            "student_name": user.name,
            "exam_name": exam.name,
            "status": log.status,
            "reason": reason,
            "confidence": confidence,
            "raw_json": log.ai_response_json,
            "attempt_count": log.attempt_count
        })
        
    return logs

from datetime import datetime, timedelta

def create_login_log(session: Session, email: str, role: str, ip_address: str, user_id: Optional[int] = None, name: Optional[str] = None, user_agent: Optional[str] = None):
    log = LoginLog(
        user_id=user_id,
        email=email,
        name=name,
        role=role,
        ip_address=ip_address,
        user_agent=user_agent
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log

def cleanup_old_login_logs(session: Session, retention_days: int = 3):
    cutoff_time = datetime.utcnow() - timedelta(days=retention_days)
    session.exec(delete(LoginLog).where(LoginLog.login_time < cutoff_time))
    session.commit()

def get_login_logs(session: Session, limit: int = 100):
    return session.exec(select(LoginLog).order_by(LoginLog.login_time.desc()).limit(limit)).all()
