from typing import Optional, List
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship

class UserRole(str, Enum):
    ADMIN = "admin"
    STUDENT = "student"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    seat_number: Optional[str] = Field(default=None, index=True) # Seat number for students, strictly mapping to excel
    role: UserRole = Field(default=UserRole.STUDENT)
    
    scores: List["Score"] = Relationship(back_populates="user")

class ExamType(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    is_mandatory: bool = Field(default=False)
    
    scores: List["Score"] = Relationship(back_populates="exam_type")

class Score(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    exam_type_id: int = Field(foreign_key="examtype.id")
    score: float
    
    user: User = Relationship(back_populates="scores")
    exam_type: ExamType = Relationship(back_populates="scores")
