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

    # Request offline access to get a refresh token.
    # prompt="select_account consent" ensures the user can choose a different account
    # and re-approve permissions.
    return await oauth.google.authorize_redirect(
        request,
        redirect_uri,
        scope="openid email profile https://www.googleapis.com/auth/gmail.send",
        access_type="offline",
        prompt="select_account consent"
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

    # Extract user info to identify the sender
    user_info = token.get('userinfo')
    sender_email = None
    if user_info and 'email' in user_info:
        sender_email = user_info['email']

    # If not in userinfo (depends on flow), try to parse from id_token or fetch?
    # Authlib usually populates userinfo if 'openid email' is in scope.
    # If not, we might need to fetch it manually, but 'openid' scope should handle it.

    # Save the email for display
    if sender_email:
        request.session['gmail_sender_email'] = sender_email

    # Store the token in the session.
    # Clean up large fields
    if 'id_token' in token:
        del token['id_token']
    if 'userinfo' in token:
        del token['userinfo']

    request.session['gmail_token'] = token

    return RedirectResponse(url='/admin?email_auth=success')
