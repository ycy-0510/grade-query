from sqlmodel import create_engine, SQLModel, Session
import os
import time
from sqlalchemy.exc import OperationalError

# Connect args needed for some mysql drivers, but pymysql fits well with sqlmodel
DATABASE_URL = os.environ.get("DATABASE_URL")

engine = create_engine(DATABASE_URL, echo=True)

from sqlalchemy import text

def init_db():
    max_retries = 30
    for i in range(max_retries):
        try:
            SQLModel.metadata.create_all(engine)
            migrate_db() # Run migration after create_all
            print("Database connected and initialized.")
            break
        except OperationalError as e:
            if i == max_retries - 1:
                print("Max retries reached. Could not connect to database.")
                raise e
            print(f"Database not ready, waiting... ({i+1}/{max_retries})")
            time.sleep(2)

def migrate_db():
    """
    Simple migration script to update schema for existing databases.
    """
    with engine.connect() as connection:
        # Check if 'is_open_for_submission' column exists in 'examtype' table
        try:
            # This query tries to select the column. If it fails, we need to add it.
            # Using limit 0 to avoid fetching data.
            connection.execute(text("SELECT is_open_for_submission FROM examtype LIMIT 0"))
        except Exception:
            print("Migrating: Adding 'is_open_for_submission' to 'examtype' table.")
            try:
                connection.execute(text("ALTER TABLE examtype ADD COLUMN is_open_for_submission BOOLEAN DEFAULT 0"))
                connection.commit()
            except Exception as e:
                print(f"Migration error: {e}")

        # Check for 'submission_deadline' column
        try:
            connection.execute(text("SELECT submission_deadline FROM examtype LIMIT 0"))
        except Exception:
            print("Migrating: Adding 'submission_deadline' to 'examtype' table.")
            try:
                connection.execute(text("ALTER TABLE examtype ADD COLUMN submission_deadline DATETIME DEFAULT NULL"))
                connection.commit()
            except Exception as e:
                print(f"Migration error (submission_deadline): {e}")
                
        # Fix for Data too long error in SubmissionLog
        try:
            # We blindly attempt to upgrade the column type to TEXT. 
            # If table doesn't exist yet, this throws but that's handled by create_all later.
            # If table exists, this upgrades it.
            connection.execute(text("ALTER TABLE submissionlog MODIFY COLUMN ai_response_json TEXT"))
            connection.commit()
        except Exception as e:
            # Table might not exist yet, or other error. 
            # Check if table exists? Or just ignore.
            # If table doesn't exist, create_all will create it with TEXT because of new model.
            pass

def get_session():
    with Session(engine) as session:
        yield session
