from fastapi import APIRouter, FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.helpers import validate_container_number, get_schedule, format_vladivostok_time, parse_datetime  
from app.db import engine, Base, get_db, dadatoken
from sqlalchemy import select, delete , update, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from app.models import User, Counterparty, CargoOrder, UserRole, Port, TransportType, Equipment, Container, Company, CargoItem, Voyage, Vessel, Notification
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
    dependencies=[Depends(get_current_user)],
    tags=["customer"]
)

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["vlad_time"] = format_vladivostok_time


# --- УВЕДОМЛЕНИЯ ---
@router.get("/notifications/badge", response_class=HTMLResponse)
async def notifications_badge(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Счётчик непрочитанных для колокольчика в шапке (опрашивается раз в минуту)."""
    result = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False
        )
    )
    count = result.scalar() or 0
    if not count:
        return HTMLResponse("")
    return HTMLResponse(
        '<span class="absolute -top-2 -right-2 bg-red-500 text-white text-[10px] font-bold '
        f'rounded-full min-w-5 h-5 px-1 flex items-center justify-center">{count if count < 100 else "99+"}</span>'
    )


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Последние уведомления пользователя. Открытие списка помечает их прочитанными."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(100)
    )
    notifications = result.scalars().all()

    response = templates.TemplateResponse(
        request=request,
        name="partials/notifications.html",
        context={
            "notifications": notifications,
            # Оператору ссылка ведёт на обработку заявки, клиенту — на её просмотр
            "staff": current_user.role in (UserRole.OPERATOR, UserRole.ADMIN),
        }
    )

    # Шаблон уже отрисован с пометками «новое» — теперь можно гасить счётчик
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    response.headers["HX-Trigger"] = "notificationsRead"
    return response
