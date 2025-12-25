from sqlmodel import create_engine, SQLModel, Session
import os
import time
from sqlalchemy.exc import OperationalError

# Connect args needed for some mysql drivers, but pymysql fits well with sqlmodel
DATABASE_URL = os.environ.get("DATABASE_URL")

engine = create_engine(DATABASE_URL, echo=True)

def init_db():
    max_retries = 30
    for i in range(max_retries):
        try:
            SQLModel.metadata.create_all(engine)
            print("Database connected and initialized.")
            break
        except OperationalError as e:
            if i == max_retries - 1:
                print("Max retries reached. Could not connect to database.")
                raise e
            print(f"Database not ready, waiting... ({i+1}/{max_retries})")
            time.sleep(2)

def get_session():
    with Session(engine) as session:
        yield session
