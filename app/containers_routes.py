from fastapi import APIRouter, FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks, Query
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
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
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx, html, json
from xhtml2pdf import pisa
from io import BytesIO
from app.config import settings
fit = settings.FIT

router = APIRouter(
    prefix="/containers",
    dependencies=[Depends(verify_auth_cookie)],
    tags=["containers"]
)
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def list_containers(
    request: Request, 
    db: AsyncSession = Depends(get_db)
):
    """
    Основной эндпоинт. 
    Если запрос от HTMX (hx-request), отдаем только кусок таблицы.
    Если обычный — полную страницу.
    """
    # Загружаем контейнеры вместе со связанным типом оборудования (equipment)
    query = (
        select(Container)
            .options(
                joinedload(Container.equipment),
                joinedload(Container.order)
                    .joinedload(CargoOrder.owner)
                    .joinedload(User.company)
            )
            .order_by(Container.id.desc())
    )    
    result = await db.execute(query)
    containers = result.scalars().all()

    # Проверяем заголовок HTMX
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(name="containers/containers_list.html", request=request, context={"containers": containers})
        
    return templates.TemplateResponse(name="containers/containers_page.html", request=request, context={})


@router.get("/{container_id}/edit", response_class=HTMLResponse)
async def edit_container_form(
    container_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # Защита, если нужна
):
    # Достаем контейнер из базы
    result = await db.execute(select(Container).where(Container.id == container_id))
    container = result.scalars().first()
    
    if not container:
        return HTMLResponse("Контейнер не найден", status_code=404)

    # Рендерим модалку и передаем туда объект контейнера для предзаполнения полей
    return templates.TemplateResponse(
        name="containers/container_edit_modal.html",
        request=request,
        context={"container": container}
    )

@router.post("/{container_id}/edit", response_class=HTMLResponse)
async def update_container(
    container_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # Ваша защита ролей
):
    # Достаем контейнер
    result = await db.execute(select(Container).where(Container.id == container_id))
    container = result.scalars().first()
    if not container:
        raise HTTPException(status_code=404, detail="Контейнер не найден")

    # Читаем данные из формы
    form = await request.form()
    
    # Обновляем поля модели
    container.container_number = form.get("container_number", "").strip()
    container.weight_gross = int(float(form.get("weight_gross") or 0))
    container.pieces = int(float(form.get("pieces") or 0))
    container.seal = form.get("seal", "").strip() or None
    container.pin_code = form.get("pin_code", "").strip() or None
    container.cargo_description = form.get("cargo_description", "").strip() or None
    
    # Сюда можно добавить вашу функцию валидации номера, если она есть
    # container.valid_number = validate_container_number(container.container_number)

    await db.commit()
    await db.refresh(container) # Чтобы подтянулись связанные объекты (например, order)
    query = (
        select(Container)
            .options(
                joinedload(Container.equipment),
                joinedload(Container.order)
                    .joinedload(CargoOrder.owner)
                    .joinedload(User.company)
            )
            .where(Container.id == container_id)
    )
    result = await db.execute(query)
    container = result.scalars().first()
    # Рендерим обратно ИМЕННО ОДНУ СТРОКУ таблицы с новыми данными
    response = templates.TemplateResponse(
        name="containers/container_row.html",  # Вынесите верстку <tr> в отдельный файлик строки
        request=request,
        context={"container": container}
    )
    
    # Вешаем триггер на закрытие модалки
    response.headers["HX-Trigger"] = "closeModal" 
    return response

@router.post("/{container_id}/cancel", response_class=HTMLResponse)
async def cancel_container(
    request: Request,
    container_id: int,
    cancel_reason: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    HTMX-эндпоинт для отмены контейнера.
    Возвращает ТОЛЬКО одну обновленную строку `tr`, которая заменит старую в браузере.
    """
    query = (
        select(Container)
        .where(Container.id == container_id)
        .options(
            joinedload(Container.equipment),
            joinedload(Container.order)
                .joinedload(CargoOrder.owner)
                .joinedload(User.company)
        )
    )
    result = await db.execute(query)
    container = result.scalar_one_or_none()
    
    if not container:
        return HTMLResponse(status_code=404, content="Контейнер не найден")
        
    # Обновляем поля модели
    container.is_cancelled = True
    container.cancel_reason = cancel_reason
    container.cancelled_at = datetime.utcnow()
    # container.cancelled_by_id = current_user.id # Если есть авторизация
    
    await db.commit()
    await db.refresh(container)
    
    # Возвращаем только одну строчку! HTMX заменит её по id автоматически (Outer HTML)
    return templates.TemplateResponse(
        name="containers/container_row.html", 
        request=request,
        context={"container": container}
    )

@router.post("/{container_id}/restore", response_class=HTMLResponse)
async def restore_container(
    request: Request,
    container_id: int,
    db: AsyncSession = Depends(get_db)
):
    # 1. Загружаем контейнер со всей цепочкой связей для Jinja
    query = (
        select(Container)
        .where(Container.id == container_id)
        .options(
            joinedload(Container.equipment),
            joinedload(Container.order)
                .joinedload(CargoOrder.owner)
                .joinedload(User.company)
        )
    )
    result = await db.execute(query)
    container = result.scalar_one_or_none()
    
    if not container:
        return HTMLResponse(status_code=404, content="Контейнер не найден")
        
    # 2. Сбрасываем флаги отмены
    container.is_cancelled = False
    container.cancel_reason = None
    container.cancelled_at = None
    container.cancelled_by_id = None
    
    # 3. Сохраняем в PostgreSQL
    await db.commit()
    
    # 4. Возвращаем ожившую строку таблицы обратно в HTMX
    return templates.TemplateResponse(
        name="containers/container_row.html", 
        request=request,
        context={"container": container}
    )