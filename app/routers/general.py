from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.i18n import TRANSLATIONS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals['translations'] = TRANSLATIONS

# --- Main Routes ---

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = request.session.get('user')
    if user:
        if user['role'] == 'admin':
            return RedirectResponse(url='/admin')
        else:
            return RedirectResponse(url='/student')
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("login.html", {"request": request, "lang": lang})

@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("privacy_policy.html", {"request": request, "lang": lang})

@router.get("/tos", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    lang = request.cookies.get("lang", "en")
    return templates.TemplateResponse("terms_of_service.html", {"request": request, "lang": lang})

@router.get("/set-language/{lang}")
async def set_language(lang: str, request: Request):
    if lang not in ["en", "zh"]:
        lang = "en"
    response = RedirectResponse(url=request.headers.get("referer", "/"))
    response.set_cookie(key="lang", value=lang, max_age=31536000) # 1 year
    return response

@router.get("/health")
async def health_check():
    return {"status": "ok"}
