from fastapi import APIRouter, FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.helpers import validate_container_number
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
from app.helpers import get_schedule
from app.config import settings
fit = settings.FIT
from zoneinfo import ZoneInfo
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

# Регистрируем фильтр локального времени Владивостока
def format_vladivostok_time(dt_utc):
    if not dt_utc:
        return ""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
    local_dt = dt_utc.astimezone(ZoneInfo("Asia/Vladivostok"))
    return local_dt.strftime("%d.%m.%Y %H:%M")

def parse_datetime(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


templates.env.filters["vlad_time"] = format_vladivostok_time

router = APIRouter(prefix="/voyages", redirect_slashes=False, tags=["Operator Voyages"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

@router.get("/", response_class=HTMLResponse)
async def list_voyages(request: Request, db: AsyncSession = Depends(get_db)):
    """Выводит список всех рейсов с предзагруженными связями для шаблона."""
    
    # Добавляем options(selectinload(...)) для всех связанных таблиц
    stmt = (
        select(Voyage)
        .options(
            selectinload(Voyage.vessel),
            selectinload(Voyage.departure_port),
            selectinload(Voyage.destination_port),
            selectinload(Voyage.containers)
        )
        .order_by(Voyage.departure_date.desc()) # Сортируем: свежие рейсы сверху
    )
    
    result = await db.execute(stmt)
    voyages = result.scalars().all()
    return templates.TemplateResponse(
        name = "voyages/table_rows.html",
        request=request,
        context={"voyages": voyages}
    )

@router.get("/{voyage_id}/edit", response_class=HTMLResponse)
async def edit_voyage_form(request: Request, voyage_id: int, db: AsyncSession = Depends(get_db)):
    # Загружаем рейс вместе со связанным судном, чтобы отобразить его имя в инпуте
    result = await db.execute(
        select(Voyage)
        .options(selectinload(Voyage.vessel))
        .where(Voyage.id == voyage_id)
    )
    voyage = result.scalar_one_or_none()
    
    if not voyage:
        raise HTTPException(status_code=404, detail="Рейс не найден")
    
    # Также нам нужны списки портов для выпадающих списков select
    ports_result = await db.execute(select(Port).order_by(Port.name))
    ports = ports_result.scalars().all()
    
    # Рендерим ту же самую форму, но передаем объект voyage
    return templates.TemplateResponse(
        name="voyages/form.html", 
        request=request,
        context={
            "voyage": voyage, 
            "ports": ports
        }
    )

@router.get("/schedule-modal", response_class=HTMLResponse)
async def get_schedule_modal(request: Request, db: AsyncSession = Depends(get_db)):
    # 1. Получаем список всех активных портов для селектов POL/POD
    ports_result = await db.execute(select(Port).order_by(Port.name))
    ports = ports_result.scalars().all()
    
    # 2. Рендерим и возвращаем HTML-код модалки по новому стандарту
    return templates.TemplateResponse(
        name="voyages/schedule_modal.html",
        request=request,
        context={"ports": ports}
    )

@router.post("/import-schedule")
async def import_schedule(
    request: Request,
    selected_voyages: list[str] = Form([]), 
    pol_id: int = Form(...),
    pod_id: int = Form(...),
    order_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db)  # Используем AsyncSession
):
    inserted_voyages = []
    
    for item in selected_voyages:
        # Парсим строку параметров
        date_from_str, date_to_str, vessel_name, voyage_number = item.split('|')
        clean_vessel_name = vessel_name.strip()
        
        # 1. Асинхронный запрос SQLAlchemy 2.0 для проверки судна (регистронезависимо)
        stmt = select(Vessel).where(Vessel.name.ilike(clean_vessel_name))
        result = await db.execute(stmt)
        vessel = result.scalars().first()
        
        if not vessel:
            # Если судна нет — создаем
            vessel = Vessel(name=clean_vessel_name)
            db.add(vessel)
            await db.flush()  # Асинхронно генерируем vessel.id в БД
            
        # Парсим даты
        date_from = datetime.strptime(date_from_str, "%d.%m.%Y").date()
        date_to = datetime.strptime(date_to_str, "%d.%m.%Y").date()
        
        # 2. Создаем рейс
        new_voyage = Voyage(
            vessel_id=vessel.id,
            number=voyage_number.strip(),
            voyage_date=date_from,
            departure_date=date_from,
            arrival_date=date_to,
            departure_port_id=pol_id,
            destination_port_id=pod_id
        )
        db.add(new_voyage)
        inserted_voyages.append(new_voyage)
        
    # Фиксируем транзакцию в базе данных
    await db.commit()
    
    # 3. Подгружаем связанные объекты Vessel для корректного рендеринга строк таблицы
    # (чтобы внутри vessel_row.html работал вызов типа {{ voyage.vessel.name }})
    #for voyage in inserted_voyages:
    #    await db.refresh(voyage, ["vessel"])
    voyage_ids = [v.id for v in inserted_voyages]
    
    stmt = (
        select(Voyage)
        .where(Voyage.id.in_(voyage_ids))
        .options(
            selectinload(Voyage.vessel),
            selectinload(Voyage.departure_port),
            selectinload(Voyage.destination_port),
            selectinload(Voyage.containers)
        )
    )
    result = await db.execute(stmt)

    inserted_voyages = result.scalars().all()


    
    # 4. Рендерим HTML-строки для отправки в HTMX
    response_html = ""
    for voyage in inserted_voyages:
        # Корректный TemplateResponse по новому стандарту FastAPI
        rendered_row = templates.TemplateResponse(
            name="voyages/vessel_row.html",
            request=request,
            context={"voyage": voyage}
        ).body.decode("utf-8")
        response_html += rendered_row
        
    # Возвращаем результат и триггерим закрытие модалки на фронтенде
    response = Response(content=response_html, media_type="text/html")
    response.headers["HX-Trigger"] = "close-voyage-modal"
    
    return response



@router.get("/new", response_class=HTMLResponse)
async def new_voyage_form(request: Request, db: AsyncSession = Depends(get_db)):
    """Отдает HTML-форму создания нового рейса с заполненными списками судов и портов."""
    
    # 1. Получаем список всех судов из базы
    vessels_result = await db.execute(select(Vessel).order_by(Vessel.name))
    vessels = vessels_result.scalars().all()
    
    # 2. Получаем список всех портов из базы
    ports_result = await db.execute(select(Port).order_by(Port.name))
    ports = ports_result.scalars().all()
    
    # 3. Рендерим шаблон формы. 
    # Передаем voyage=None, чтобы форма поняла, что это СОЗДАНИЕ, а не редактирование.
    return templates.TemplateResponse(
        name="voyages/form.html", 
        request=request,
        context={
            "request": request, 
            "vessels": vessels, 
            "ports": ports, 
            "voyage": None
        }
    )

@router.get("/vessels/search", response_class=HTMLResponse)
async def search_vessels(request: Request, db: AsyncSession = Depends(get_db)):
    # Забираем поисковый запрос (первый параметр q)
    search_query = next(iter(request.query_params.values()), "").strip()
    
    if not search_query:
        # База пустая или поле пустое — выводим топ по использованию
        query = (
            select(Vessel)
            .order_by(Vessel.last_used_at.desc(), Vessel.voyage_count.desc())
            .limit(5)
        )
        result = await db.execute(query)
        vessels = result.scalars().all()
        
        return templates.TemplateResponse(
            name="voyages/vessel_search_results.html",
            request=request,
            context={"vessels": vessels, "is_history": True}
        )
    
    # Сценарий Б: Оператор вводит текст — ищем по подстроке
    query = (
        select(Vessel)
        .where((Vessel.name.ilike(f"%{search_query}%")) | (Vessel.name_eng.ilike(f"%{search_query}%")))
        .limit(5)
    )
    result = await db.execute(query)
    vessels = result.scalars().all()

    return templates.TemplateResponse(
        name="voyages/vessel_search_results.html", 
        request=request,
        context={"vessels": vessels, "is_history": False}
    )

@router.post("/", response_class=HTMLResponse)
async def create_voyage(
    request: Request,
    number: str = Form(...),
    voyage_date: date = Form(...),
    departure_date: date = Form(...),
    arrival_date: date = Form(...),
    vessel_id: int = Form(...),  # Приходит из скрытого поля формы
    departure_port_id: int = Form(...),
    destination_port_id: int = Form(...),
    db: AsyncSession = Depends(get_db)
):
    # 1. Загружаем судно, чтобы обновить его счетчики оптимизации
    vessel_result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
    vessel = vessel_result.scalar_one_or_none()
    
    if vessel:
        vessel.voyage_count += 1
        vessel.last_used_at = datetime.now()  # Записываем время использования

    # 2. Создаем сам рейс
    db_voyage = Voyage(
        number=number,
        voyage_date=voyage_date,
        departure_date=departure_date,
        arrival_date=arrival_date,
        vessel_id=vessel_id,
        departure_port_id=departure_port_id,
        destination_port_id=destination_port_id
    )
    db.add(db_voyage)
    
    # Сохраняем всё в базу одной транзакцией
    await db.commit()
    
    # 3. Подгружаем связи для корректного рендеринга строки таблицы (иначе Jinja2 выдаст ошибку)
    stmt = (
        select(Voyage)
        .options(
            selectinload(Voyage.vessel),
            selectinload(Voyage.departure_port),
            selectinload(Voyage.destination_port),
            selectinload(Voyage.containers)
        )
        .where(Voyage.id == db_voyage.id)
    )
    fresh_voyage_result = await db.execute(stmt)
    fresh_voyage = fresh_voyage_result.scalar_one()

    # Возвращаем ОДНУ новую HTML-строку. 
    # HTMX сам вставит её в конец таблицы благодаря hx-swap="beforeend"
    response = templates.TemplateResponse(
        name = "voyages/vessel_row.html",
        request = request,
        context = {"voyage": fresh_voyage}
    )
    response.headers["HX-Trigger"] = "close-voyage-modal"
    return response


@router.get("/vessels/new", response_class=HTMLResponse)
async def new_vessel_form(request: Request, q: str = Query(default="")):
    """Открывает мини-модалку добавления судна, предзаполняя название."""
    return templates.TemplateResponse(
        name="voyages/vessel_form.html",
        request=request,
        context={"preset_name": q}
    )

# POST: Сохранить судно
@router.post("/vessels/", response_class=HTMLResponse)
async def create_vessel(
    request: Request,
    name: str = Form(...),
    name_eng: str = Form(...),
    description: str = Form(default=None),
    db: AsyncSession = Depends(get_db)
):
    new_vessel = Vessel(name=name, name_eng=name_eng, description=description)
    db.add(new_vessel)
    await db.commit()
    await db.refresh(new_vessel)

    trigger_data = {
        "vesselCreated": {
            "id": new_vessel.id,
            "name": f"{new_vessel.name}"# ({new_vessel.name_eng})"
        }
    }

    
    return HTMLResponse(content="", headers={"HX-Trigger": json.dumps(trigger_data)})


# --- 3. ОБНОВЛЕНИЕ ДАННЫХ РЕЙСА (PUT) ---
@router.put("/{voyage_id}", response_class=HTMLResponse)
async def update_voyage(
    request: Request,
    voyage_id: int,
    number: str = Form(...),
    vessel_id: int = Form(...),
    departure_port_id: int = Form(...),
    destination_port_id: int = Form(...),
    voyage_date: str = Form(...),       # Принимаем как строку, парсим при необходимости
    departure_date: str = Form(...),
    arrival_date: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    # Находим редактируемый рейс
    result = await db.execute(select(Voyage).where(Voyage.id == voyage_id))
    voyage = result.scalar_one_or_none()
    
    if not voyage:
        raise HTTPException(status_code=404, detail="Рейс не найден")
    
    # Обновляем поля модели
    voyage.number = number
    voyage.vessel_id = vessel_id
    voyage.departure_port_id = departure_port_id
    voyage.destination_port_id = destination_port_id
    
    # Если в БД используются типы Date, не забудьте преобразовать строку из формы:
    # from datetime import datetime
    # voyage.departure_date = datetime.strptime(departure_date, "%Y-%m-%d").date()
    voyage.voyage_date = voyage_date
    voyage.departure_date = departure_date
    voyage.arrival_date = arrival_date
    
    await db.commit()
    
    # Подгружаем связанные объекты (судно, порты) для корректного рендеринга строки таблицы
    updated_result = await db.execute(
        select(Voyage)
        .options(
            selectinload(Voyage.vessel),
            selectinload(Voyage.departure_port),
            selectinload(Voyage.destination_port),
            selectinload(Voyage.containers) # Чтобы length работал корректно
        )
        .where(Voyage.id == voyage_id)
    )
    updated_voyage = updated_result.scalar_one()
    
    # Возвращаем обновленный кусок строки. HTMX заменит старую строку новой благодаря outerHTML
    return templates.TemplateResponse(
        name="voyages/vessel_row.html", 
        request=request,
        context={"voyage": updated_voyage},
        headers={"HX-Trigger": "close-voyage-modal"} # Закрываем модалку после сохранения
    )

@router.delete("/{voyage_id}", response_class=HTMLResponse)
async def delete_voyage(voyage_id: int, db: AsyncSession = Depends(get_db)):
    # Ищем рейс в базе данных
    result = await db.execute(select(Voyage).where(Voyage.id == voyage_id))
    voyage = result.scalar_one_or_none()
    
    if not voyage:
        raise HTTPException(status_code=404, detail="Рейс не найден")
    
    # Удаляем и фиксируем
    await db.delete(voyage)
    await db.commit()
    
    # HTMX ожидает пустой ответ или статус 200, чтобы удалить hx-target элемент из DOM
    return HTMLResponse(content="", status_code=status.HTTP_200_OK)