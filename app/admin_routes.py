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
    prefix="/admin",
    dependencies=[Depends(verify_auth_cookie)],
    tags=["admin"]
)

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["vlad_time"] = format_vladivostok_time


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)): #current_user: User = Depends(get_current_user)):
    try:
        current_user = await get_current_user(request, db)
        if (current_user.role == UserRole.USER):
            return RedirectResponse(url="/", status_code=303)

        result = await db.execute(select(Company))
        
        companies = result.scalars().all()
        res_users = await db.execute(
            select(User).options(joinedload(User.company))
        )
        users = res_users.scalars().all()

        return templates.TemplateResponse(
            request=request,
            name="admin/users.html",
            context={"user": current_user, "companies": companies, "users": users}
        )
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    

@router.get("/dashboard")
async def operator_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    try:
        current_user = await get_current_user(request, db)
        
        # Проверка прав: если не админ и не оператор — на выход
        if current_user.role not in [UserRole.ADMIN, UserRole.OPERATOR]:
            #return RedirectResponse(url="/", status_code=303)
            return Response(headers={"HX-Redirect": "/"})

        result = await db.execute(
            select(CargoOrder)
            .options(
                joinedload(CargoOrder.port_of_loading),
                joinedload(CargoOrder.port_of_discharge),
                joinedload(CargoOrder.pre_carriage_carrier),
                # Добавляем загрузку оборудования для отображения в списке
                selectinload(CargoOrder.containers).selectinload(Container.equipment),
                joinedload(CargoOrder.owner).joinedload(User.company)
            )
            .where(CargoOrder.status != "draft")
            .order_by(CargoOrder.id.desc())
        )
        orders = result.scalars().all()

        return templates.TemplateResponse(
            request=request,
            name="operator/dashboard.html", # Лучше держать в папке operator, чтобы не путать с клиентским
            context={"user": current_user, "orders": orders}
        )
    except HTTPException:
        response = RedirectResponse(url="/login", status_code=303)
        response.headers["HX-Redirect"] = "/login" # Заставит HTMX сделать полный редирект
        return response

# Получение формы
@router.get("/edit-user-form/{user_id}")
async def get_user_form(request: Request, user_id: int = None, db: AsyncSession = Depends(get_db)):
    user = None
    if user_id:
        res = await db.execute(select(User).where(User.id == user_id))
        user = res.scalar_one_or_none()
    
    comp_res = await db.execute(select(Company).where(Company.is_deleted == False))
    companies = comp_res.scalars().all()
    
    return templates.TemplateResponse(request=request, name="admin/edit_user_form.html", context={
        "user": user, "companies": companies
    })

@router.get("/create-user-form")
async def get_create_user_form(request: Request, db: AsyncSession = Depends(get_db)):
    # Загружаем компании, чтобы привязать нового юзера к одной из них
    comp_res = await db.execute(select(Company).where(Company.is_deleted == False))
    companies = comp_res.scalars().all()
    
    return templates.TemplateResponse(request=request, name="admin/edit_user_form.html", context={
        "user": None,  # Важно: передаем None
        "companies": companies
    })


# Сохранение/Обновление
@router.post("/update-user-form/{user_id}")
async def handle_user_save(request: Request, user_id: int = None, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    
    if user_id:
        res = await db.execute(select(User).where(User.id == user_id))
        user = res.scalar_one_or_none()
    else:
        user = User(role=UserRole.USER)
        db.add(user)

    user.email = form.get("email")
    user.name = form.get("full_name")
    user.company_id = int(form.get("company_id")) if form.get("company_id") else None
    
    if form.get("password"):
        user.hashed_password = hash_password(form.get("password"))

    await db.commit()
    await db.refresh(user)
    
    # Чтобы в строке отобразилось имя компании, нужно её подгрузить
    res = await db.execute(select(User).options(joinedload(User.company)).where(User.id == user.id))
    user = res.scalar_one()

    response = templates.TemplateResponse(request=request, name="admin/user_row.html", context={"user": user})
    response.headers["HX-Trigger"] = "closeModal"
    return response

@router.delete("/delete-user/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user:
        #await db.delete(user)
        user.is_active = False
        await db.commit()
    return HTMLResponse(content="")


@router.post("/create-user")
async def admin_create_user(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(None),
    company_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Проверка на админа
    if current_user.role != UserRole.ADMIN:
        return HTMLResponse(content="<p class='text-red-500'>Доступ запрещен</p>", status_code=403)
    
    # Проверка дубликата
    result = await db.execute(select(User).where(User.email == email))
    if result.scalars().first():
        return HTMLResponse(content="<p class='text-red-500'>Пользователь уже существует</p>")

    # Создание (используем твой hash_password)
    new_user = User(
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.USER,
        name=full_name,
        company_id=company_id
    )
    db.add(new_user)
    await db.commit()
    
    response = HTMLResponse(content="")
    response.headers["HX-Trigger"] = "closeModal"
    return response


@router.get("/edit-company/{company_id}")
async def get_edit_company_form(request: Request, company_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    
    # Экранируем поля, где могут быть кавычки
    safe_name = html.escape(company.name or "")
    safe_fullname = html.escape(company.fullname or "")
    
    return templates.TemplateResponse(request=request, name="admin/edit_company_form.html", context={"company": company})

@router.post("/update-company/{company_id}")
async def update_company(
    company_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db)
):
    form_data = await request.form()
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    
    if not company:
        return HTMLResponse("Компания не найдена", status_code=404)

    # Обновляем все поля из формы
    for field in ["name", "fullname", "inn", "kpp", "ogrn", "address1", "address2", 
                  "tel1", "tel2", "email1", "email2", "bik", "ks", "rs"]:
        setattr(company, field, form_data.get(field))

    await db.commit()
    
    # Возвращаем обновленную строку таблицы (как в прошлом примере)
    response = templates.TemplateResponse(request=request, name="admin/company_row.html", context={"company": company})
    response.headers["HX-Trigger"] = "closeModal, updateCompanies"
    return response

@router.delete("/delete-company/{company_id}")
async def delete_company(company_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    
    if company:
        company.is_deleted = True # Просто ставим флаг
        await db.commit()
    
    # Возвращаем пустую строку, чтобы HTMX удалил элемент из списка
    return HTMLResponse(content="")

@router.get("/create-company-form")
async def get_create_company_form(request: Request):
    # Передаем None вместо объекта company, чтобы поля были пустыми
    return templates.TemplateResponse(request=request, name="admin/edit_company_form.html", context={
        "company": None  # В шаблоне используем {{ company.name or '' }}
    })

@router.get("/search-company-dadata")
async def search_company_dadata(request: Request):
    query = next(iter(request.query_params.values()), "").strip()
    if len(query) < 3:
        return HTMLResponse("")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party",
            headers={"Authorization": f"Token {dadatoken}", "Content-Type": "application/json"},
            json={"query": query, "count": 5}
        )
    
    suggestions = resp.json().get("suggestions", []) if resp.status_code == 200 else []
    return templates.TemplateResponse(request=request, name="company_search_results.html", context={"suggestions": suggestions})

@router.post("/create-company")
async def create_company(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in [UserRole.ADMIN, UserRole.OPERATOR]:
        return HTMLResponse('<span class="text-red-500">Доступ запрещен</span>', status_code=403)

    form = await request.form()
    inn = form.get("inn")

    # Проверка на дубликат (если ИНН указан)
    if inn:
        existing = await db.execute(select(Company).where(Company.inn == inn))
        if existing.scalars().first():
            return HTMLResponse('<span class="text-amber-500">Компания с таким ИНН уже существует</span>')

    # Создаем объект компании, мапим поля из формы на модель
    new_company = Company(
        name=form.get("name"),
        fullname=form.get("fullname"),
        inn=form.get("inn"),
        kpp=form.get("kpp"),
        ogrn=form.get("ogrn"),
        address1=form.get("address1"),
        tel1=form.get("tel1"),
        email1=form.get("email1"),
        bik=form.get("bik"),
        ks=form.get("ks"),
        rs=form.get("rs")
    )
    
    db.add(new_company)
    await db.commit()
    
    response = HTMLResponse(content="")
    response.headers["HX-Trigger"] = "closeModal"
    return response
