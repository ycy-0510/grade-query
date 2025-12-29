from fastapi import Request, HTTPException, Depends
import secrets
from app.models import UserRole

# --- Security Dependency ---

def get_current_user(request: Request):
    return request.session.get('user')

def is_admin(request: Request):
    user = get_current_user(request)
    if not user or user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="Unauthorized")
    return user

async def csrf_protect(request: Request):
    """
    Dependency to enforce CSRF protection on POST requests.
    Validates that the '_csrf_token' in the form data matches the session token.
    """
    if request.method == "POST":
        # 1. Get token from session
        session_token = request.session.get("csrf_token")
        if not session_token:
            # Should trigger if session expired or not set
            raise HTTPException(status_code=403, detail="CSRF Session Token Missing")

        # 2. Get token from form or header
        incoming_token = None

        # Check Header first (common for AJAX/JSON)
        incoming_token = request.headers.get("X-CSRF-Token")

        if not incoming_token:
            # Fallback to Form Data (for standard HTML forms)
            try:
                form = await request.form()
                incoming_token = form.get("csrf_token")
            except Exception:
                # If body is JSON or can't be parsed as form, ignore form check
                pass

        # 3. Compare safely
        if not incoming_token or not secrets.compare_digest(session_token, incoming_token):
             raise HTTPException(status_code=403, detail="CSRF Token Invalid")
