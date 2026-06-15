from fastapi import APIRouter, FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.helpers import validate_container_number, get_schedule, format_vladivostok_time, parse_datetime, verify_auth_cookie  
from app.db import engine, Base, get_db, dadatoken
from sqlalchemy import select, delete , update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from app.models import User, Counterparty, CargoOrder, UserRole, Port, TransportType, Equipment, Container, Company, CargoItem, Voyage, Vessel
from app.auth import verify_password, create_access_token, get_current_user, hash_password 
from app.schemas import Token, UserLogin, CounterpartyCreate, CounterpartyRead, OrderRead, UserCreate
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx, html, json
from xhtml2pdf import pisa
from io import BytesIO
from app.config import settings
fit = settings.FIT

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(verify_auth_cookie)],
    tags=["customer"]
)

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["vlad_time"] = format_vladivostok_time

