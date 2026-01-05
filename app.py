# backend/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend import models  # init_db runs on import
from backend.routes import router

app = FastAPI(title="Exam System")

# mount static files (مسیر نسبی به پوشه‌ای که این فایل قرار دارد)
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

# templates (اگر لازم شد در کد رندر مستقیم هم استفاده می‌شود)
templates = Jinja2Templates(directory="backend/templates")

# include app routes
app.include_router(router)