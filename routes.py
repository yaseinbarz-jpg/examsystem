# backend/routes.py
import os
import json
import re
import hmac
import time
import hashlib
import urllib.parse
from typing import Optional, Dict
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.templating import Jinja2Templates

from backend import models

router = APIRouter()
BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---------------- Teacher Credentials (unchanged) ----------------
_TEACHER_USER = "admin"
_TEACHER_PASS = "1234"

# ---------------- Security / Config ----------------
# If your deployment uses TLS (recommended), keep True.
# If you're testing locally without HTTPS, set to False temporarily.
FORCE_SECURE_COOKIES = True

# Cookie name
_TEACHER_COOKIE_NAME = "teacher_auth"

# Cookie signature secret (deterministic so restarts don't log everyone out).
# We derive it from the hardcoded password + base dir to avoid adding a new secret file.
_SECRET_KEY = hashlib.sha256((str(_TEACHER_PASS) + "|" + BASE_DIR).encode()).digest()

# Cookie expiry (seconds) — e.g., 7 days
TEACHER_COOKIE_MAX_AGE = 7 * 24 * 3600

# Rate-limiter simple in-memory (IP -> list[timestamps])
_RATE_LIMIT_STORE: Dict[str, list] = {}

# Rate-limit params (can be tuned)
LOGIN_RATE_LIMIT = {"calls": 5, "per_seconds": 60}        # 5 attempts per minute per IP
SUBMIT_RATE_LIMIT = {"calls": 30, "per_seconds": 60}     # 30 submissions per minute per IP

def _get_client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For if behind proxy; otherwise fallback to client.host
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # take the first IP
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

def _rate_limited(ip: str, key: str, calls: int, per_seconds: int) -> bool:
    """Simple sliding-window rate limiter keyed by ip+endpoint key."""
    store_key = f"{ip}:{key}"
    now = time.time()
    window_start = now - per_seconds
    timestamps = _RATE_LIMIT_STORE.get(store_key, [])
    # keep only ones inside window
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= calls:
        # rate limited
        _RATE_LIMIT_STORE[store_key] = timestamps
        return True
    timestamps.append(now)
    _RATE_LIMIT_STORE[store_key] = timestamps
    return False

# ---------------- cookie signing helpers ----------------
def _sign_value(value: str) -> str:
    """
    Return value|timestamp|hmac where hmac = HMAC_SHA256(secret, value|timestamp).
    """
    ts = str(int(time.time()))
    payload = f"{value}|{ts}"
    sig = hmac.new(_SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"

def _verify_signed_value(signed: str, max_age: Optional[int] = None) -> Optional[str]:
    """
    Verify and return original value if ok and not expired.
    signed format: value|timestamp|hmac
    """
    try:
        parts = signed.split("|")
        if len(parts) < 3:
            return None
        # value may contain pipes if templates include them, so join accordingly
        sig = parts[-1]
        ts = parts[-2]
        value = "|".join(parts[:-2])
        payload = f"{value}|{ts}"
        expected = hmac.new(_SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        if max_age is not None:
            if int(ts) + int(max_age) < int(time.time()):
                return None
        return value
    except Exception:
        return None

# ---------------- Teacher auth helpers ----------------
def require_teacher(request: Request) -> Optional[RedirectResponse]:
    """
    Returns None if allowed, otherwise RedirectResponse to login.
    This verifies the signed cookie.
    """
    cookie = request.cookies.get(_TEACHER_COOKIE_NAME)
    if not cookie:
        return RedirectResponse("/teacher", status_code=status.HTTP_303_SEE_OTHER)
    val = _verify_signed_value(cookie, max_age=TEACHER_COOKIE_MAX_AGE)
    if val == "1":
        return None
    return RedirectResponse("/teacher", status_code=status.HTTP_303_SEE_OTHER)

def _set_teacher_cookie(resp: RedirectResponse):
    """
    Set a signed secure cookie on the response. Keep path '/' to match previous behavior.
    """
    signed = _sign_value("1")
    # FastAPI/Starlette set_cookie params:
    resp.set_cookie(
        key=_TEACHER_COOKIE_NAME,
        value=signed,
        path="/",
        httponly=True,
        secure=FORCE_SECURE_COOKIES,
        samesite="strict",
        max_age=TEACHER_COOKIE_MAX_AGE,
    )

def _delete_teacher_cookie(resp: RedirectResponse):
    resp.delete_cookie(_TEACHER_COOKIE_NAME, path="/")

# ---------------- Teacher routes ----------------
@router.get("/teacher", response_class=HTMLResponse)
def teacher_login_page(request: Request):
    # if already logged in, redirect
    if request.cookies.get(_TEACHER_COOKIE_NAME):
        # verify signature (if invalid treat as not logged in)
        val = _verify_signed_value(request.cookies.get(_TEACHER_COOKIE_NAME), max_age=TEACHER_COOKIE_MAX_AGE)
        if val == "1":
            return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("teacher.html", {"request": request, "message": request.query_params.get("message")})

@router.post("/teacher/login")
def teacher_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # rate-limit by IP
    ip = _get_client_ip(request)
    if _rate_limited(ip, "teacher_login", LOGIN_RATE_LIMIT["calls"], LOGIN_RATE_LIMIT["per_seconds"]):
        msg = urllib.parse.quote("تعداد تلاش‌ها بیش از حد است. بعداً تلاش کنید.")
        return RedirectResponse(f"/teacher?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    # keep existing credential variables, but compare in constant-time
    user_ok = hmac.compare_digest(username, _TEACHER_USER)
    pass_ok = hmac.compare_digest(password, _TEACHER_PASS)
    if user_ok and pass_ok:
        resp = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        _set_teacher_cookie(resp)
        return resp
    return RedirectResponse("/teacher?message=نام_کاربری_یا_رمز_اشتباه", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/teacher/logout")
def teacher_logout(request: Request):
    resp = RedirectResponse("/teacher", status_code=status.HTTP_303_SEE_OTHER)
    _delete_teacher_cookie(resp)
    return resp

# ---------------- Dashboard / Teacher pages ----------------
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if (redir := require_teacher(request)) is not None:
        return redir
    exams = models.list_exams()
    return templates.TemplateResponse("dashboard.html", {"request": request, "exams": exams})

@router.post("/dashboard/exam/add")
def add_exam_route(request: Request, title: str = Form(...), description: str = Form("")):
    if (redir := require_teacher(request)) is not None:
        return redir
    eid = models.add_exam(title, description)
    return RedirectResponse(f"/dashboard/exam/{eid}/questions", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/dashboard/exam/delete")
def delete_exam_route(request: Request, exam_id: int = Form(...)):
    if (redir := require_teacher(request)) is not None:
        return redir
    models.delete_exam(exam_id)
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/dashboard/exam/{exam_id}/questions", response_class=HTMLResponse)
def manage_questions(request: Request, exam_id: int):
    if (redir := require_teacher(request)) is not None:
        return redir
    exam = models.get_exam(exam_id)
    questions = models.get_exam_questions(exam_id)
    return templates.TemplateResponse("dashboard_exam.html", {"request": request, "exam": exam, "questions": questions})

@router.post("/dashboard/exam/{exam_id}/questions/add")
def add_question_route(request: Request, exam_id: int,
                       text: str = Form(...),
                       option_a: str = Form(""),
                       option_b: str = Form(""),
                       option_c: str = Form(""),
                       option_d: str = Form(""),
                       correct: str = Form(""),
                       topic: str = Form("")):
    if (redir := require_teacher(request)) is not None:
        return redir
    models.add_question(exam_id, text, option_a, option_b, option_c, option_d, correct, topic)
    return RedirectResponse(f"/dashboard/exam/{exam_id}/questions", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/dashboard/question/delete")
def delete_question_route(request: Request, exam_id: int = Form(...), question_id: int = Form(...)):
    if (redir := require_teacher(request)) is not None:
        return redir
    models.delete_question(question_id)
    return RedirectResponse(f"/dashboard/exam/{exam_id}/questions", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/dashboard/results/{exam_id}", response_class=HTMLResponse)
def view_results(request: Request, exam_id: int, province: Optional[str] = None):
    if (redir := require_teacher(request)) is not None:
        return redir
    results = models.get_results(exam_id, province)  # رتبه استانی دقیق
    provinces = models.list_provinces()
    return templates.TemplateResponse("results.html", {"request": request, "results": results, "exam_id": exam_id, "provinces": provinces, "selected_province": province})

@router.get("/teacher/students/{exam_id}", response_class=HTMLResponse)
def teacher_student_list(request: Request, exam_id: int, province: Optional[str] = None):
    if (redir := require_teacher(request)) is not None:
        return redir
    results = models.get_results(exam_id, province)
    provinces = models.list_provinces()
    return templates.TemplateResponse("student_list.html", {"request": request, "results": results, "exam_id": exam_id, "provinces": provinces, "selected_province": province})

# ---------------- Student ----------------
@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    provinces = models.list_provinces()
    exams = models.list_exams()
    message = request.query_params.get("message")
    return templates.TemplateResponse("index.html", {"request": request, "provinces": provinces, "exams": exams, "message": message})

def normalize_name_phone_from_input(input_str: str):
    if not input_str:
        return ("", "", "")
    combined, phone = models.normalize_combined_input(input_str)
    if "_" in combined:
        name = combined.rsplit("_", 1)[0]
    else:
        name = combined
    return (combined, name, phone)

@router.post("/start")
def start_exam(
    request: Request,
    exam_id: int = Form(...),
    province: str = Form(...),
    student_input: Optional[str] = Form(None),
    student_name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
):
    province = (province or "").strip()
    try:
        exam_id = int(exam_id)
    except Exception:
        msg = urllib.parse.quote("آزمون نامعتبر است.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    # validate province against known list
    provinces_allowed = models.list_provinces()
    if province not in provinces_allowed:
        msg = urllib.parse.quote("استان نامعتبر است.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    if student_input:
        combined, name, phone_extracted = normalize_name_phone_from_input(student_input)
        name_val = name or (student_name or "").strip()
        phone_val = phone_extracted or (phone or "").strip()
        combined_val = combined or name_val
    else:
        name_val = (student_name or "").strip()
        phone_val = (phone or "").strip()
        combined_val = f"{name_val}_{phone_val}" if name_val and phone_val else name_val or ""

    if not combined_val or not name_val or not phone_val or not province:
        msg = urllib.parse.quote("نام و شماره تلفن و استان باید وارد شوند.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    if not models.is_persian_name_strict(name_val):
        msg = urllib.parse.quote("لطفاً نام را فقط با حروف فارسی وارد کنید (بدون عدد یا علامت).")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    phone_clean = re.sub(r"[^\d\u06F0-\u06F9]", "", phone_val)
    if not re.fullmatch(r"[\u06F0-\u06F9]+", phone_clean):
        msg = urllib.parse.quote("لطفاً شماره تلفن را با ارقام فارسی وارد کنید.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    phone_norm = models.normalize_phone(phone_clean)
    if not phone_norm:
        msg = urllib.parse.quote("شماره تلفن معتبر وارد کنید.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    existing = models.has_exact_duplicate(exam_id, name_val, phone_norm, province)
    if existing:
        return RedirectResponse(
            f"/result/{existing['id']}?student_name={urllib.parse.quote(existing['student_name'])}&province={urllib.parse.quote(existing['province'])}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    q_name = urllib.parse.quote(combined_val)
    q_province = urllib.parse.quote(province)
    return RedirectResponse(
        f"/exam/{exam_id}?student_name={q_name}&province={q_province}&phone={urllib.parse.quote(phone_norm)}",
        status_code=status.HTTP_303_SEE_OTHER
    )

@router.get("/exam/{exam_id}", response_class=HTMLResponse)
def take_exam(request: Request, exam_id: int, student_name: Optional[str] = None, province: Optional[str] = None, phone: Optional[str] = None):
    questions = models.get_exam_questions(exam_id)
    duration_seconds = len(questions) * 60 if questions else 60
    return templates.TemplateResponse("exam.html", {"request": request, "questions": questions, "exam_id": exam_id, "student_name": student_name, "province": province, "phone": phone, "duration": duration_seconds})

@router.post("/submit")
def submit_exam(
    request: Request,
    exam_id: int = Form(...),
    student_name: str = Form(...),
    province: str = Form(...),
    phone: Optional[str] = Form(None),
    answers_json: Optional[str] = Form(None),
):
    # basic rate-limit
    ip = _get_client_ip(request)
    if _rate_limited(ip, "submit", SUBMIT_RATE_LIMIT["calls"], SUBMIT_RATE_LIMIT["per_seconds"]):
        msg = urllib.parse.quote("ارسال‌ها بیش از حد است، لطفاً بعداً تلاش کنید.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    # validate province
    provinces_allowed = models.list_provinces()
    if province not in provinces_allowed:
        msg = urllib.parse.quote("استان نامعتبر است.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    try:
        if not answers_json:
            answers = {}
        else:
            # prevent huge payloads (simple protection)
            if len(answers_json) > 5000:
                answers = {}
            else:
                parsed = json.loads(answers_json)
                # limit number of keys to avoid huge objects
                if isinstance(parsed, dict) and len(parsed) <= 2000:
                    answers = parsed
                else:
                    answers = {}
    except Exception:
        answers = {}

    phone_val = phone or request.query_params.get("phone", "")
    phone_norm = models.normalize_phone(phone_val)

    existing = models.has_exact_duplicate(exam_id, student_name, phone_norm, province)
    if existing:
        return RedirectResponse(
            f"/result/{existing['id']}?student_name={urllib.parse.quote(existing['student_name'])}&province={urllib.parse.quote(existing['province'])}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    try:
        rid = models.save_result(exam_id, student_name, phone_norm, province, answers)
    except Exception:
        msg = urllib.parse.quote("خطا در ذخیره نتیجه. لطفاً دوباره تلاش کنید.")
        return RedirectResponse(f"/?message={msg}", status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(f"/result/{rid}?student_name={urllib.parse.quote(student_name)}&province={urllib.parse.quote(province)}", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/student/result/{exam_id}", response_class=HTMLResponse)
def student_view_result(request: Request, exam_id: int, province: Optional[str] = None):
    results = models.get_results(exam_id, province)
    provinces = models.list_provinces()
    return templates.TemplateResponse("results.html", {
        "request": request,
        "results": results,
        "exam_id": exam_id,
        "provinces": provinces,
        "selected_province": province,
        "back_url": "/"
    })

@router.get("/result/{result_id}", response_class=HTMLResponse)
def show_result(request: Request, result_id: int):
    res = models.get_result_by_id(result_id)
    if not res:
        return templates.TemplateResponse("result.html", {"request": request, "result": None, "details": {"per_topic": {}, "per_question": {}}, "back_url": "/"})
    details = res.get("details")
    if not isinstance(details, dict):
        details = {"per_topic": {}, "per_question": {}}
    else:
        details.setdefault("per_topic", {})
        details.setdefault("per_question", {})
    return templates.TemplateResponse("result.html", {
        "request": request,
        "result": res,
        "details": details,
        "back_url": "/"
    })