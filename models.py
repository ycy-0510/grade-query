from typing import Optional, List
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship

class UserRole(str, Enum):
    ADMIN = "admin"
    STUDENT = "student"

from datetime import datetime

class SubmissionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    seat_number: Optional[str] = Field(default=None, index=True) # Seat number for students, strictly mapping to excel
    role: UserRole = Field(default=UserRole.STUDENT)
    
    scores: List["Score"] = Relationship(back_populates="user")
    submissions: List["SubmissionLog"] = Relationship(back_populates="user")

class ExamType(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    is_mandatory: bool = Field(default=False)
    is_open_for_submission: bool = Field(default=False)
    submission_deadline: Optional[datetime] = Field(default=None)
    
    scores: List["Score"] = Relationship(back_populates="exam_type")
    submissions: List["SubmissionLog"] = Relationship(back_populates="exam_type")

class Score(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    exam_type_id: int = Field(foreign_key="examtype.id")
    score: float
    
    user: User = Relationship(back_populates="scores")
    exam_type: ExamType = Relationship(back_populates="scores")

from sqlalchemy import Text, Column

class SubmissionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    exam_type_id: int = Field(foreign_key="examtype.id")
    attempt_count: int = Field(default=0)
    last_attempt_time: datetime = Field(default_factory=datetime.utcnow)
    status: SubmissionStatus = Field(default=SubmissionStatus.PENDING)
    ai_response_json: Optional[str] = Field(default=None, sa_column=Column(Text)) # Store JSON response for debugging
    
    user: User = Relationship(back_populates="submissions")
    exam_type: ExamType = Relationship(back_populates="submissions")

class LoginLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id") # Optional link
    email: str = Field(index=True)
    name: Optional[str] = None
    role: str
    ip_address: str
    user_agent: Optional[str] = None
    login_time: datetime = Field(default_factory=datetime.utcnow)
