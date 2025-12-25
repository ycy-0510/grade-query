from typing import Optional
from models import SubmissionLog, User, ExamType
from sqlmodel import select, Session

def get_submission_logs(session: Session, user_id: Optional[int] = None, limit: int = 100):
    """
    Fetches submission logs.
    If user_id is provided, filters by that user.
    """
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
