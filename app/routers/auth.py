from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlmodel import Session
import os

from app.core.database import get_session
from app.models import User, UserRole
from app.core.auth import oauth
from app.crud import create_user, get_user_by_email, create_login_log

router = APIRouter()

def get_real_ip(request: Request) -> str:
    # Cloudflare Tunnel / Proxy Real IP Support
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]
    return request.client.host

# --- Auth Routes ---
@router.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for('auth_google')
    # Conditionally force HTTPS if running behind a proxy handling SSL
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        redirect_uri = str(redirect_uri).replace("http://", "https://")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/auth/google")
async def auth_google(request: Request, session: Session = Depends(get_session)):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
            # Try to fetch from userinfo endpoint if not in token
            user_info = await oauth.google.userinfo(token=token)
    except Exception as e:
        import html
        return HTMLResponse(f"Auth failed: {html.escape(str(e))}", status_code=400)

    email = user_info['email']
    name = user_info['name']

    # Check User
    db_user = get_user_by_email(session, email)

    # Initial Admin Seeding (Quick Hack)
    initial_admin = os.environ.get("INITIAL_ADMIN_EMAIL")
    if not db_user and initial_admin and email == initial_admin:
        # Auto-create admin
        db_user = User(email=email, name=name, role=UserRole.ADMIN)
        db_user = create_user(session, db_user)

    if not db_user:
        return HTMLResponse("<h1>Unauthorized</h1><p>Your email is not in the system.</p>", status_code=401)

    # Create Session
    request.session['user'] = {
        'id': db_user.id,
        'email': db_user.email,
        'name': db_user.name,
        'role': db_user.role,
        'seat_number': db_user.seat_number
    }


    if db_user.role == UserRole.ADMIN:
        # Create Login Log
        create_login_log(
            session,
            email=db_user.email,
            role=db_user.role,
            ip_address=get_real_ip(request),
            user_id=db_user.id,
            name=db_user.name,
            user_agent=request.headers.get("user-agent")
        )
        return RedirectResponse(url='/admin')
    else:
        # Create Login Log
        create_login_log(
            session,
            email=db_user.email,
            role=db_user.role,
            ip_address=get_real_ip(request),
            user_id=db_user.id,
            name=db_user.name,
            user_agent=request.headers.get("user-agent")
        )
        return RedirectResponse(url='/student')

@router.get("/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')
