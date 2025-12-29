from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlmodel import Session
from app.core.database import get_session
from app.core.auth import oauth
from app.models import UserRole
import os
import json
from datetime import datetime

router = APIRouter()

@router.get("/admin/auth/gmail")
async def auth_gmail(request: Request):
    """
    Initiates Google OAuth flow specifically for Gmail sending permissions.
    """
    user = request.session.get('user')
    if not user or user['role'] != UserRole.ADMIN:
        return RedirectResponse("/")

    # Redirect URI for the callback
    redirect_uri = request.url_for('auth_gmail_callback')

    # Handle Proxy/HTTPS
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        redirect_uri = str(redirect_uri).replace("http://", "https://")

    # Request offline access to get a refresh token so we don't have to re-auth constantly?
    # For now, let's just get the access token.
    # Usually "access_type": "offline" and "prompt": "consent" are needed for refresh token.
    return await oauth.google.authorize_redirect(
        request,
        redirect_uri,
        scope="openid email profile https://www.googleapis.com/auth/gmail.send",
        access_type="offline",
        prompt="consent"
    )

@router.get("/admin/auth/gmail/callback")
async def auth_gmail_callback(request: Request, session: Session = Depends(get_session)):
    user = request.session.get('user')
    if not user or user['role'] != UserRole.ADMIN:
        return RedirectResponse("/")

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        return HTMLResponse(f"Auth failed: {str(e)}", status_code=400)

    # Store the token in the session.
    # Ideally, we should encrypt this or store it in DB against the user.
    # For this implementation, putting it in session is the quickest path,
    # but we must ensure the cookie size limits aren't hit.
    # The token dict contains: access_token, refresh_token, expires_at, etc.
    # It might be large.

    # Filter to essential parts to save space?
    # We need 'access_token', 'refresh_token', 'token_type', 'expires_at'.
    # id_token is large and we don't need it for Gmail API calls (only for login).

    gmail_creds = {
        'access_token': token.get('access_token'),
        'refresh_token': token.get('refresh_token'),
        'token_type': token.get('token_type'),
        'expiry': token.get('expires_at') # key might differ depending on lib, usually 'expires_at' or 'expires_in'
    }

    # Use 'expires_in' to calculate absolute time if needed, but google-auth usually wants the dict as is.
    # Let's just store the whole thing minus id_token to be safe on size?
    if 'id_token' in token:
        del token['id_token']
    if 'userinfo' in token:
        del token['userinfo']

    request.session['gmail_token'] = token

    return RedirectResponse(url='/admin?email_auth=success')
