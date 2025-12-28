# Grade Query System

A FastAPI-based high school grade query system with distinct roles for Students and Admins, powered by Google OAuth.

## Features

-   **Google OAuth Authentication**: Secure login for all users.
-   **Role-Based Access Control**:
    -   **Students**: View their own grades, top 20 average, and detailed reports.
    -   **Admins**: Import students/grades, configure exams, and manage the database.
-   **Data Import/Export**:
    -   Import students and grades via Excel.
    -   Full database backup/restore (JSON).
    -   Export student grades with calculated averages (Excel).
-   **Modern UI**: Responsive design using Tailwind CSS.

## Prerequisites

-   Docker & Docker Compose
-   Google Cloud Console Project (for OAuth credentials)
-   [uv](https://docs.astral.sh/uv/) (Dependency Manager)

## Development Setup

1.  **Install dependencies**:
    ```bash
    uv sync
    ```

2.  **Activate virtual environment**:
    ```bash
    source .venv/bin/activate
    ```

3.  **Run locally**:
    ```bash
    uvicorn main:app --reload
    ```

## Quick Start

1.  **Clone the repository** (to get the `docker-compose.yml`):
    ```bash
    git clone <repository_url>
    cd grade-query-system
    ```

2.  **Configuration**:
    Create a `.env` file in the root directory and add your configuration:
    ```bash
    touch .env
    ```
    ```env
    # Database Configuration
    MYSQL_ROOT_PASSWORD=rootpassword
    MYSQL_DATABASE=grade_system
    # Note: Keep the host as 'db'
    DATABASE_URL=mysql+pymysql://root:rootpassword@db:3306/grade_system

    # Google OAuth (Get these from Google Cloud Console)
    GOOGLE_CLIENT_ID=your_google_client_id
    GOOGLE_CLIENT_SECRET=your_google_client_secret

    # Security
    SECRET_KEY=your_secret_key_here
    
    # Initial Admin Setup
    INITIAL_ADMIN_EMAIL=your_email@gmail.com

    GEMINI_API_KEY=your_key_here
    GEMINI_MODEL=gemini-2.5-flash
    TURNSTILE_SITE_KEY=your_site_key
    TURNSTILE_SECRET_KEY=your_secret_key
    ```

3.  **Run the Application**:
    This will pull the pre-built image from Docker Hub (`ycy10/grade-system`) and start the services.
    ```bash
    docker-compose up -d
    ```
    The application will be available at `http://localhost:8000`.

## Admin Usage

### Dashboard Overview
-   **Import Students**: Upload an Excel file with `Seat Number`, `Name`, `Email`.
-   **Upload Grades**: Upload one or more Excel files where the first column is `Seat Number` and subsequent columns are Exam Names (e.g., `Math`, `English`).
-   **Exam Configurations**: Toggle "Mandatory" status for exams. Mandatory exams are always included in the Top 20 calculation.
-   **Database Management**:
    -   **Backup**: Download a generic JSON dump of the database.
    -   **Restore**: Upload a JSON dump to *replace* the current database (Destructive action!).
    -   **Export Grades**: Download an Excel sheet with all students and their "Top 20 Average".

### Score Management
-   Click **"View & Edit Scores"** to see a master table of all students and exams.
-   Edit scores directly in the table and click **"Save Changes"**.

## Student Usage

-   Log in with your school-associated Google account.
-   View your "Top 20 Average" and a breakdown of which exam scores were used in the calculation.
