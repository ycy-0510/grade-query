from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import os
import secrets
from app.core.database import init_db, engine
from app.core.i18n import TRANSLATIONS
from app.crud import cleanup_old_login_logs
from sqlmodel import Session
from app.routers import auth, admin, student, general, admin_email_auth, admin_email_api
from fastapi.responses import JSONResponse
from fastapi import Depends
from app.dependencies import is_admin

# Disable default OpenAPI
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

templates = Jinja2Templates(directory="app/templates")
templates.env.globals['translations'] = TRANSLATIONS

@app.middleware("http")
async def add_security_headers_and_csrf(request: Request, call_next):
    # Ensure CSRF token exists in session
    # NOTE: SessionMiddleware must be installed and wrapping this middleware for this to work.
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)

    response = await call_next(request)

    # Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"

    return response

# Add SessionMiddleware LAST so it is the OUTERMOST middleware (runs first)
SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe_dev_secret")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Include Routers
app.include_router(general.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(admin_email_auth.router)
app.include_router(admin_email_api.router)
app.include_router(student.router)

@app.on_event("startup")
def on_startup():
    init_db()
    # Cleanup old logs (3 days retention)
    with Session(engine) as session:
        cleanup_old_login_logs(session, retention_days=3)

@app.get("/admin/openapi.json", include_in_schema=False)
async def get_admin_openapi_json(request: Request, user: dict = Depends(is_admin)):
    from fastapi.openapi.utils import get_openapi
    return JSONResponse(get_openapi(title="Grade Query System", version="1.0.0", routes=app.routes))
