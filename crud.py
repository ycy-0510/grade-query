from sqlmodel import Session, select
from models import User, ExamType, Score, UserRole
import pandas as pd
from typing import List, Dict, Any, Tuple
import json
from sqlmodel import Session, select, delete

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

def calculate_student_grades(user_id: int, session: Session) -> Dict[str, Any]:
    """
    Implements the Top 20 Rule.
    """
    user = session.get(User, user_id)
    if not user:
        return {}
        
    # Fetch all scores with exam info
    statement = select(Score, ExamType).join(ExamType).where(Score.user_id == user_id)
    results = session.exec(statement).all()
    
    mandatory_items = []
    optional_items = []
    
    formatted_rows = []
    
    for score, exam in results:
        item = {
            "exam_name": exam.name,
            "score": score.score,
            "is_mandatory": exam.is_mandatory,
            "included": False # Will be updated
        }
        
        if exam.is_mandatory:
            mandatory_items.append(item)
        else:
            optional_items.append(item)
        
        formatted_rows.append(item) # Keep a reference to these dicts to update 'included'
        
    # Logic
    # 3. Select ALL Mandatory
    selected_scores = []
    for item in mandatory_items:
        item["included"] = True
        selected_scores.append(item["score"])
        
    # 4. Calculate remaining slots
    slots_needed = 20 - len(mandatory_items)
    
    # 5. Select Top Slots from Optional
    if slots_needed > 0:
        # Sort optionals by score descending
        optional_items.sort(key=lambda x: x["score"], reverse=True)
        top_optionals = optional_items[:slots_needed]
        
        for item in top_optionals:
            item["included"] = True
            selected_scores.append(item["score"])
            
    # Calculate Average
    effective_count = len(selected_scores)
    total_score = sum(selected_scores)
    
    # Requirement: If fewer than 20 scores, fill with 0 (divide by 20).
    # If more than 20 (e.g. 21 mandatory exams), then divide by actual count.
    # Usually Top 20 implies count is exactly 20 unless we have > 20 mandatory?
    # Spec "choose highest 20, but if insufficient 20, fill 0 calculation" -> implies divisor is max(20, count).
    
    divisor = max(20, effective_count)
    
    if divisor == 0:
        average = 0.0
    else:
        average = total_score / divisor
        
    return {
        "user_name": user.name,
        "seat_number": user.seat_number,
        "average": round(average, 2),
        "exam_count": len(selected_scores), # Effective count (might be less than 20 if total exams < 20)
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
    exams = session.exec(select(ExamType).order_by(ExamType.id)).all()
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
    Exports all Users, ExamTypes, and Scores to a dictionary.
    """
    users = session.exec(select(User)).all()
    exams = session.exec(select(ExamType)).all()
    scores = session.exec(select(Score)).all()
    
    data = {
        "users": [u.model_dump() for u in users],
        "exams": [e.model_dump() for e in exams],
        "scores": [s.model_dump() for s in scores]
    }
    return data

def import_db_from_json(data: Dict[str, Any], session: Session) -> Dict[str, Any]:
    """
    Imports data from dictionary. WARNING: Deletes existing data.
    """
    stats = {"users": 0, "exams": 0, "scores": 0, "errors": 0}
    
    try:
        # 1. Clear existing data
        # Order matters due to foreign keys: Score -> (User, ExamType)
        session.exec(delete(Score))
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
            
        # Commit users and exams first so IDs exist for scores
        # We need to ensure we insert with explicit IDs. 
        # SQLModel/SQLAlchemy usually allows inserting explicit IDs.
        session.commit()
        
        # 4. Import Scores
        scores_data = data.get("scores", [])
        for s_data in scores_data:
            score = Score.model_validate(s_data)
            session.add(score)
            stats["scores"] += 1
            
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
    
    # Get all exams for columns headers (sorted by ID)
    all_exams = session.exec(select(ExamType).order_by(ExamType.id)).all()
    
    data = []
    
    for student in students:
        # Calculate grade report
        # Note: This executes a query per student. Acceptable for typical school sizes.
        report = calculate_student_grades(student.id, session)
        
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
