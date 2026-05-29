from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.helpers import validate_container_number
from app.db import engine, Base, get_db, dadatoken
from sqlalchemy import select, delete , update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from app.models import User, Counterparty, CargoOrder, UserRole, Port, TransportType, Equipment, Container, Company, CargoItem
from app.auth import verify_password, create_access_token, get_current_user, hash_password 
from app.schemas import Token, UserLogin, CounterpartyCreate, CounterpartyRead, OrderRead, UserCreate
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx, html
from xhtml2pdf import pisa
from io import BytesIO
from app.helpers import get_schedule
from app.config import settings
fit = settings.FIT

import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


from zoneinfo import ZoneInfo
from fastapi.templating import Jinja2Templates

SMTP_SERVER = "smtp.mail.ru"
SMTP_PORT = 25
SMTP_USER = "example@mail.ru"
SMTP_PASSWORD = "password"


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

app = FastAPI(title="CargoFlow API")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user = await get_current_user(request, db)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"user": user}
        )
    except HTTPException:
        return RedirectResponse(url="/login")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/1", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user = await get_current_user(request, db)
        
        # Если это админ или оператор — кидаем в панель управления
        if user.role in [UserRole.ADMIN, UserRole.OPERATOR]:
            return RedirectResponse(url="/api/operator/dashboard", status_code=303)
        
        # Если обычный клиент — кидаем в его список заказов
        return RedirectResponse(url="/api/orders", status_code=303)
        
    except HTTPException:
        # Если не залогинен — на страницу входа
        return RedirectResponse(url="/login")
    
@app.get("/me")
async def read_user_me(token: str = Depends(oauth2_scheme)):
    return {"token": token, "info": "This route is seen by lock only"}


@app.post("/api/logout")
async def logout():#response: Response):
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key="access_token", path="/")
    return response

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/api/login")
async def login_browser(
    response: Response, 
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).filter(User.email == form_data.username))
    user = result.scalars().first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        #raise HTTPException(status_code=401, detail="Error")
        return HTMLResponse(
            content="Неверный email или пароль", 
            status_code=200 # Важно оставить 200, чтобы HTMX обработал ответ
        )
    
    token = create_access_token(data={"sub": user.email, "role": user.role.value})
    res = Response()
    res.headers["HX-Redirect"] = "/" # Это заставит браузер перейти на главную
    res.set_cookie(key="access_token", value=token, httponly=True)
    return res

@app.get("/admin", response_class=HTMLResponse)
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




# Получение формы
@app.get("/api/admin/edit-user-form/{user_id}")
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

@app.get("/api/admin/create-user-form")
async def get_create_user_form(request: Request, db: AsyncSession = Depends(get_db)):
    # Загружаем компании, чтобы привязать нового юзера к одной из них
    comp_res = await db.execute(select(Company).where(Company.is_deleted == False))
    companies = comp_res.scalars().all()
    
    return templates.TemplateResponse(request=request, name="admin/edit_user_form.html", context={
        "user": None,  # Важно: передаем None
        "companies": companies
    })


# Сохранение/Обновление
@app.post("/api/admin/update-user-form/{user_id}")
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

@app.delete("/api/admin/delete-user/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user:
        #await db.delete(user)
        user.is_active = False
        await db.commit()
    return HTMLResponse(content="")


@app.post("/api/admin/create-user")
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


@app.get("/api/admin/edit-company/{company_id}")
async def get_edit_company_form(request: Request, company_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    
    # Экранируем поля, где могут быть кавычки
    safe_name = html.escape(company.name or "")
    safe_fullname = html.escape(company.fullname or "")
    
    return templates.TemplateResponse(request=request, name="admin/edit_company_form.html", context={"company": company})

@app.post("/api/admin/update-company/{company_id}")
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

@app.delete("/api/admin/delete-company/{company_id}")
async def delete_company(company_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    
    if company:
        company.is_deleted = True # Просто ставим флаг
        await db.commit()
    
    # Возвращаем пустую строку, чтобы HTMX удалил элемент из списка
    return HTMLResponse(content="")

@app.get("/api/admin/create-company-form")
async def get_create_company_form(request: Request):
    # Передаем None вместо объекта company, чтобы поля были пустыми
    return templates.TemplateResponse(request=request, name="admin/edit_company_form.html", context={
        "company": None  # В шаблоне используем {{ company.name or '' }}
    })

@app.get("/api/v1/schedule/view", response_class=HTMLResponse)
async def schedule_view(pol_id: int = None, pod_id: int = None, order_id: int = None, db: AsyncSession = Depends(get_db)):
    # Если выбраны не оба порта, ничего не показываем
    if not pol_id or not pod_id or not order_id:
        return ""
    # 1. Находим названия портов по ID в вашей базе (для функции get_schedule)
    # pol_name = db.get_port(pol_id).name ...
    result = await db.execute(select(Port).where(Port.id == pol_id))
    port_from = result.scalar_one_or_none()
    result = await db.execute(select(Port).where(Port.id == pod_id))
    port_to = result.scalar_one_or_none()
    result_order = await db.execute(select(CargoOrder).where(CargoOrder.id == order_id))
    order = result_order.scalar_one_or_none()
    # 2. Вызываем FESCO API
    if not order or not order.loading_date:
        # Если даты в базе нет, используем текущую как запасной вариант
        search_date = datetime.now().strftime("%Y-%m-%d")
    else:
        # Приводим дату из БД (datetime/date) к строке нужного формата
        search_date = order.loading_date.strftime("%Y-%m-%d")
    pol_name = port_from.name
    pod_name = port_to.name
    data = get_schedule(token=fit, date_from=search_date, from_loc=pol_name, to_loc=pod_name)

    if not data or not data.get('data'):
        return "<div class='p-3 text-sm text-amber-600 bg-amber-50 rounded'>Рейсы не найдены</div>"

    # 3. Формируем HTML (можно через Jinja2 шаблон или f-строку)
    html = '<div class="space-y-2">'
    for ship in data['data'][0]['schedule']:
        html += f"""
        <div class="p-3 border rounded bg-white shadow-sm flex justify-between items-center text-sm">
            <div>
                <span class="font-bold">{ship['dateFrom']}</span> 
                <span class="text-gray-400">→</span> 
                <span class="font-bold">{ship['dateTo']}</span>
                <div class="text-blue-600 font-medium">{ship['transportName']}</div>
            </div>
            <div class="text-right">
                <div class="text-xs text-gray-500">Рейс: {ship['voyageNumber']}</div>
            </div>
        </div>
        """
    html += '</div>'
    return html

@app.post("/api/counterparties/get-or-create", response_model=CounterpartyRead)
async def get_or_create_counterparty(
    data: CounterpartyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Ищем по точному совпадению имени (игнорируя регистр) для конкретного юзера
    query = select(Counterparty).where(
        Counterparty.user_id == current_user.id,
        Counterparty.name.ilike(data.name.strip())
    )
    result = await db.execute(query)
    existing = result.scalars().first()

    if existing:
        return existing

    # 2. Если не нашли — создаем нового
    new_party = Counterparty(
        user_id=current_user.id,
        name=data.name.strip(),
        inn=data.inn,
        address=data.address,
        contact_info=data.contact_info
    )
    db.add(new_party)
    await db.commit()
    await db.refresh(new_party)
    return new_party


async def sync_counterparty(db, user_id, name, inn, address, contact, is_carrier=False):
    if not name or not name.strip(): return None
    
    name = name.strip()
    # Ищем существующего
    query = select(Counterparty).where(Counterparty.user_id == user_id, Counterparty.name.ilike(name))
    res = await db.execute(query)
    cp = res.scalars().first()

    if cp:
        # Обновляем данные, если клиент их подправил в форме
        cp.inn = inn
        cp.address = address
        cp.contact_info = contact
        cp.is_carrier = is_carrier
        cp.use_count += 1
    else:
        # Создаем нового
        cp = Counterparty(user_id=user_id, name=name, inn=inn, address=address, contact_info=contact, is_carrier=is_carrier)
        db.add(cp)
    
    await db.flush()
    return cp.id



@app.get("/api/counterparties/search") #, response_model=list[CounterpartyRead])
async def search_counterparties(
    request: Request, #q: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    search_query = next(iter(request.query_params.values()), "")

    if not search_query:
        return HTMLResponse("empty")
    
    result = await db.execute(
        select(Counterparty)
        .where(
            Counterparty.user_id == current_user.id,
            Counterparty.name.ilike(f"%{search_query}%")
        ).limit(5)
    )
    
    cps = result.scalars().all()
    options = "".join([f'<option value="{cp.name}">' for cp in cps])
    return HTMLResponse(content=options)

@app.get("/api/admin/search-company-dadata")
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

@app.post("/api/admin/create-company")
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


@app.get("/api/search/address")
async def search_address(request: Request):
    #query = request.query_params.get("q", "").strip()
    #query = request.query_params.get("pre_carriage_address", "").strip()
    query = next(iter(request.query_params.values()), "").strip()
    if not query or len(query) < 3:
        return HTMLResponse("")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
            headers={"Authorization": f"Token {dadatoken}", "Content-Type": "application/json"},
            json={"query": query, "count": 5}
        )
        suggestions = resp.json().get("suggestions", []) if resp.status_code == 200 else []

    # Возвращаем простой список подсказок
    html = '<div class="absolute z-50 w-full bg-white border shadow-xl rounded-md mt-1">'
    for s in suggestions:
        html += f'''
        <div class="p-2 hover:bg-blue-50 cursor-pointer border-b text-sm"
             hx-on:click="this.closest('.address-group').querySelector('input').value = '{s['value']}'; this.parentElement.remove();">
            {s['value']}
        </div>
        '''
    html += '</div>'
    return HTMLResponse(html)

@app.get("/api/search/counterparty")
async def search_cp(request: Request, is_carrier: Optional[bool] = False, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    search_query = next(iter(request.query_params.values()), "").strip()
    
    # 1. Ищем своих в БД
    query = select(Counterparty).where(Counterparty.user_id == current_user.id)
    if is_carrier:
        query = query.where(Counterparty.is_carrier == True)

    if search_query:
        query = query.where(Counterparty.name.ilike(f"%{search_query}%"))
        query = query.order_by(Counterparty.use_count.desc())
    else:
        query = query.order_by(Counterparty.use_count.desc(), Counterparty.last_use.desc())
    
    db_result = await db.execute(query.limit(5))
    local_cps = db_result.scalars().all()

    # 2. Ищем внешних в DaData (только если есть поисковый запрос)
    external_cps = []
    if search_query and len(search_query) > 2: # Не ищем по 1-2 буквам
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party",
                    headers={
                        "Authorization": f"Token {dadatoken}",
                        "Content-Type": "application/json"
                    },
                    json={"query": search_query, "count": 3}
                )
                if response.status_code == 200:
                    suggestions = response.json().get("suggestions", [])
                    # Форматируем под твою модель, чтобы шаблону было удобно
                    print(f"Найдено в DaData: {len(suggestions)}") 
                    for s in suggestions:
                        external_cps.append({
                            "is_external": True, # Флаг, что это из сети
                            "name": s["value"],
                            "inn": s["data"]["inn"],
                            "address": s["data"]["address"]["value"],
                            "raw_data": s["data"] # Для hx-on
                        })
        except Exception as e:
            print(f"DaData error: {e}")

    return templates.TemplateResponse(
        request=request,
        name = "partials/cp_search_results.html",
        context={"cps": local_cps, "external_cps": external_cps, "is_carrier": is_carrier}
    )


# Вспомогательная функция для "умного" поиска/создания
async def get_or_create_counterparty(name: str, user_id: int, db: AsyncSession, is_carrier: bool = False,  inn: Optional[str] = None, address: Optional[str] = None):
    if not name or not name.strip():
        return None
    name = name.strip()
    # Ищем существующего
    result = await db.execute(
        select(Counterparty).where(
            Counterparty.user_id == user_id,
            Counterparty.name.ilike(name)
        )
    )
    cp = result.scalars().first()
    if cp:
        # Если нашли — обновляем статистику использования
        cp.use_count += 1
        cp.last_use = datetime.utcnow()
        # Если старый контрагент теперь используется как перевозчик — дописываем ему флаг
        if is_carrier and not cp.is_carrier:
            cp.is_carrier = True
    else:
        # Создаем абсолютно нового контрагента со всей статистикой
        cp = Counterparty(
            name=name, 
            user_id=user_id,
            is_carrier=is_carrier,
            inn=inn,
            address=address,
            use_count=1,
            last_use=datetime.utcnow()
        )
        db.add(cp)
        
    await db.flush() # Выплескиваем в БД, чтобы зафиксировать изменения и получить ID
    return cp.id

@app.get("/api/search/counterpartyold")
async def search_cp(request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    search_query = next(iter(request.query_params.values()), "")
    query = select(Counterparty).where(Counterparty.user_id == current_user.id)

    if search_query:
        query = query.where(Counterparty.name.ilike(f"%{search_query}%"))
        # При обычном поиске тоже полезно сортировать по частоте
        query = query.order_by(Counterparty.use_count.desc())
    else:
        # При клике на пустое поле — топ по частоте и дате
        query = query.order_by(Counterparty.use_count.desc(), Counterparty.last_use.desc())
    
    result = await db.execute(query.limit(5))
    
    cps = result.scalars().all()
    # Возвращаем список, который при клике заполнит поля через hx-on
    return templates.TemplateResponse(request=request, name = "partials/cp_search_results.html", context={"cps": cps})


@app.post("/api/orders")
async def start_order(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="partials/step_0_rules.html",
        context={}
    )

@app.post("/api/orders/init")
async def init_order(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    form_data = await request.form()
    
    # Шаг 0: Юридическая проверка
    if form_data.get("restricted_items") != "no":
        return HTMLResponse("Перевозка запрещена", status_code=403)

    # Создаем пустой черновик заявки
    new_order = CargoOrder(owner_id=current_user.id, status="draft", transport_type="CONTAINER")
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    # Справочники для Шага 1
    ports_res = await db.execute(select(Port).where(Port.is_active == True).order_by(Port.name))
    ports = ports_res.scalars().all()
    
    eq_res = await db.execute(select(Equipment).order_by(Equipment.name))
    equipments = eq_res.scalars().all()
    
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_1_route_and_equipment.html",
        context={
            "order_id": new_order.id, 
            "order": new_order, 
            "ports": ports,
            "equipments": equipments,
            "today": today, 
            "default_date": tomorrow, 
            "current_step": "step-1"
        }
    )

@app.get("/api/orders/{order_id}/step-1")
async def get_step_1(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    if not order: 
        raise HTTPException(status_code=404)

    ports_res = await db.execute(select(Port).where(Port.is_active == True).order_by(Port.name))
    ports = ports_res.scalars().all()
    
    eq_res = await db.execute(select(Equipment).order_by(Equipment.name))
    equipments = eq_res.scalars().all()

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_1_route_and_equipment.html",
        context={
            "order": order, 
            "order_id": order_id, 
            "ports": ports, 
            "equipments": equipments,
            "today": today, 
            "default_date": tomorrow
        }
    )

@app.patch("/api/orders/{order_id}/step-1")
async def save_step_1(
    order_id: int,
    request: Request,
    pol_id: int = Form(...),
    pod_id: int = Form(...),
    loading_date: str = Form(...),
    equipment_id: int = Form(...),
    is_soc: bool = Form(False),
    needs_return: bool = Form(False),
    return_instructions: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404)

    # Записываем данные
    order.pol_id = pol_id
    order.pod_id = pod_id
    order.equipment_id = equipment_id
    order.is_soc = is_soc
    order.needs_return = needs_return if is_soc else False
    order.return_instructions = return_instructions if (is_soc and needs_return) else None
    
    if loading_date:
        order.loading_date = datetime.strptime(loading_date, "%Y-%m-%d").date()
        
    await db.commit()

    # Сразу подгружаем существующие контейнеры, если пользователь вернулся назад
    result = await db.execute(
        select(CargoOrder)
        .options(selectinload(CargoOrder.containers).selectinload(Container.items))
        .where(CargoOrder.id == order_id)
    )
    order = result.scalar_one()

    # Справочник оборудования для строк контейнеров внутри таблицы
    eq_res = await db.execute(select(Equipment))
    equipments = eq_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_2_cargo_details.html",
        context={
            "order": order,
            "order_id": order_id,
            "equipments": equipments,
            "current_step": "step-2"
        }
    )

@app.get("/api/orders/{order_id}/step-2")
async def get_step_2(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(CargoOrder)
        .options(selectinload(CargoOrder.containers).selectinload(Container.items))
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    if not order: 
        raise HTTPException(status_code=404)
    
    eq_res = await db.execute(select(Equipment))
    equipments = eq_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_2_cargo_details.html",
        context={"order": order, "order_id": order_id, "equipments": equipments}
    )

@app.patch("/api/orders/{order_id}/step-2")
async def save_step_2(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    form_data = await request.form()
    
    result = await db.execute(
        select(CargoOrder)
        .options(selectinload(CargoOrder.containers))
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404)

    # Удаляем старые контейнеры и сбрасываем кэш
    await db.execute(delete(Container).where(Container.order_id == order_id))
    await db.flush()

    # Твой парсинг динамических полей
    indices = form_data.getlist("row_idx[]")
    eq_ids = form_data.getlist("equipment_id[]")
    numbers = form_data.getlist("container_number[]")
    seals = form_data.getlist("seal[]")
    weights = form_data.getlist("weight_gross[]")
    pieces = form_data.getlist("pieces[]")
    description = form_data.getlist("cargo_description[]")
    is_lcl = form_data.getlist("is_lcl[]")
    
    vent_indices = form_data.getlist("ventilation[]")
    port_indices = form_data.getlist("port_plug[]")
    vessel_indices = form_data.getlist("vessel_plug[]")
    temps = form_data.getlist("temperature[]")
    plug_dates = form_data.getlist("plug_start_date[]")

    def get_val(lst, idx, default=None):
        return lst[idx] if idx < len(lst) else default

    for i, idx in enumerate(indices):
        new_con = Container(
            order_id=order_id,
            equipment_id=int(eq_ids[i]),
            is_soc=order.is_soc,
            is_lcl=idx in is_lcl,
            container_number=numbers[i] if i < len(numbers) else None,
            valid_number=validate_container_number(numbers[i]) if i < len(numbers) else False,
            seal=seals[i] if i < len(seals) else None,
            weight_gross=float(get_val(weights, i)) if get_val(weights, i) else 0.0,
            pieces=int(get_val(pieces, i)) if get_val(pieces, i) else 0,
            cargo_description=get_val(description, i),
            temperature=float(temps[i]) if i < len(temps) and temps[i] else None,
            ventilation=idx in vent_indices,
            port_plug=idx in port_indices,
            vessel_plug=idx in vessel_indices,
        )

        date_str = plug_dates[i] if i < len(plug_dates) else None
        if date_str:
            try:
                new_con.plug_start_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError: pass

        # Парсинг вложенных CargoItems
        item_names = form_data.getlist(f"item_name[{idx}][]")
        item_weights = form_data.getlist(f"item_weight[{idx}][]")
        item_pieces = form_data.getlist(f"item_pieces[{idx}][]")

        for j in range(len(item_names)):
            if item_names[j].strip():
                new_item = CargoItem(
                    name=item_names[j],
                    weight_gross=float(item_weights[j]) if j < len(item_weights) and item_weights[j] else 0.0,
                    pieces=int(item_pieces[j]) if j < len(item_pieces) and item_pieces[j] else 0
                )
                new_con.items.append(new_item)

        db.add(new_con)

    await db.commit()

    # Перезагружаем заказ со связанными контрагентами для отображения на Шаге 3
    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.notify_party)
        )
        .where(CargoOrder.id == order_id)
    )
    order = result.scalars().first()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_3_parties_and_trucking.html",
        context={"order_id": order_id, "order": order, "current_step": "step-3"}
    )

@app.get("/api/orders/{order_id}/step-3")
async def get_step_3(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Загружаем заказ с контрагентами, чтобы поля формы заполнились, если пользователь вернулся назад
    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.notify_party),
            joinedload(CargoOrder.pre_carriage_carrier)
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    
    if not order: 
        raise HTTPException(status_code=404, detail="Заказ не найден")

    return templates.TemplateResponse(
        request=request,
        name="partials/step_3_parties_and_trucking.html",
        context={
            "order_id": order_id, 
            "order": order,
            "current_step": "step-3"
        }
    )


@app.patch("/api/orders/{order_id}/step-3")
async def save_step_3(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    form_data = await request.form()
    
    # Ищем заказ с проверкой владельца
    result = await db.execute(
        select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # --- СИНХРОНИЗАЦИЯ КОНТРАГЕНТОВ (Твои функции поиска/создания) ---
    order.shipper_id = await sync_counterparty(
        db, current_user.id, 
        form_data.get("shipper_name"), form_data.get("shipper_inn"), 
        form_data.get("shipper_address"), form_data.get("shipper_contact")
    )
    order.consignee_id = await sync_counterparty(
        db, current_user.id, 
        form_data.get("consignee_name"), form_data.get("consignee_inn"), 
        form_data.get("consignee_address"), form_data.get("consignee_contact")
    )
    order.pre_carriage_carrier_id = await sync_counterparty(
        db, current_user.id, 
        form_data.get("carrier_name"), form_data.get("carrier_inn"), 
        form_data.get("carrier_address"), form_data.get("carrier_contact"), is_carrier=True
    )
    
    notify_name = form_data.get("notify_name")
    order.notify_party_id = await get_or_create_counterparty(notify_name, current_user.id, db) if notify_name else None

    # --- НАЗЕМНОЕ ПЛЕЧО: ОТПРАВИТЕЛЬ (Pre-carriage / Экспорт) ---
    order.pre_carriage_required = "pre_carriage_required" in form_data
    if order.pre_carriage_required:
        # Наш вывоз: пишем адрес и контакт, затираем перевозчика клиента
        order.pre_carriage_address = form_data.get("pre_carriage_address")
        order.pre_carriage_contact = form_data.get("pre_carriage_contact")
        order.pre_carriage_carrier = None
    else:
        # Свой вывоз: затираем адрес и контакт, сохраняем ТК клиента
        order.pre_carriage_address = None
        order.pre_carriage_contact = None
        if "pre_carriage_carrier" in form_data:
            order.pre_carriage_carrier = form_data.get("pre_carriage_carrier")
    
    order.pre_carriage_date = parse_datetime(form_data.get("pre_carriage_date"))
    order.pre_carriage_comment = form_data.get("pre_carriage_comment")

    # --- НАЗЕМНОЕ ПЛЕЧО: ПОЛУЧАТЕЛЬ (On-carriage / Импорт) ---
    order.on_carriage_required = "on_carriage_required" in form_data
    if order.on_carriage_required:
        # Наш вывоз «до двери»: пишем адрес и контакт, затираем ТК на релиз
        order.on_carriage_address = form_data.get("on_carriage_address")
        order.on_carriage_contact = form_data.get("on_carriage_contact")
        order.on_carriage_notes = None
    else:
        # Свой вывоз из порта: затираем адрес и контакт, сохраняем ТК в notes
        order.on_carriage_address = None
        order.on_carriage_contact = None
        order.on_carriage_notes = form_data.get("on_carriage_notes") # Сюда пишется автоперевозчик
        
    order.on_carriage_date = parse_datetime(form_data.get("on_carriage_date"))
    order.on_carriage_comment = form_data.get("on_carriage_comment")

    # Меняем статус черновика, так как базовые шаги заполнены
    #order.status = "details_filled"
    
    # Фиксируем все изменения в PostgreSQL
    await db.commit()

    # --- ЗАГРУЗКА ДАННЫХ ДЛЯ ШАГА 4 (Сводная информация) ---
    # Делаем один мощный запрос, подтягивая вообще ВСЕ связи через joinedload/selectinload
    final_result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            joinedload(CargoOrder.shipper),
            joinedload(CargoOrder.consignee),
            joinedload(CargoOrder.notify_party),
            joinedload(CargoOrder.pre_carriage_carrier),
            selectinload(CargoOrder.containers).options(
                selectinload(Container.items),
                selectinload(Container.equipment)
            )
        )
        .where(CargoOrder.id == order_id)
    )
    full_order = final_result.scalars().first()

    # Отдаем Шаг 4 (order_summary.html)
    return templates.TemplateResponse(
        request=request,
        name="partials/order_summary.html", 
        context={
            "order_id": order_id, 
            "order": full_order, 
            "current_step": "step-4"
        }
    )

@app.get("/api/orders/{order_id}/step-4")
async def get_step_4(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Загружаем заказ со всей цепочкой связанных данных
    result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            joinedload(CargoOrder.shipper),
            joinedload(CargoOrder.consignee),
            joinedload(CargoOrder.notify_party),
            joinedload(CargoOrder.pre_carriage_carrier),
            # Вытягиваем контейнеры, а внутри них — грузы и типы оборудования
            selectinload(CargoOrder.containers).options(
                selectinload(Container.items),
                selectinload(Container.equipment)
            )
        )
        .where(
            CargoOrder.id == order_id, 
            CargoOrder.owner_id == current_user.id # Защита от чужих глаз
        )
    )
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # 2. Отдаем шаблон сводной информации по заказу
    return templates.TemplateResponse(
        request=request,
        name="partials/order_summary.html", 
        context={
            "order_id": order_id, 
            "order": order,
            "current_step": "step-4"
        }
    )



@app.get("/api/orders/{order_id}/add-container-row")
async def add_container_row(request: Request, order_id: int, index: int = 0, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Теперь index берется из JS-параметра кнопки
    eq_res = await db.execute(select(Equipment))
    equipments = eq_res.scalars().all()
    order_res = await db.execute(select(CargoOrder).where(CargoOrder.id == order_id))
    order = order_res.scalars().first()
    if not order:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        request=request,
        name="partials/container_row.html", 
        context={
            "equipments": equipments, 
            "loop_index": index, # Передаем полученный индекс
            "container": None,
            "order": order
        }
    )



@app.get("/api/orders")
async def list_orders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Загружаем заказы вместе со связанными портами для красоты
    result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(
            CargoOrder.owner_id == current_user.id,
            CargoOrder.is_valid == True
        )
        .order_by(CargoOrder.id.desc())
    )
    orders = result.scalars().all()
    
    return templates.TemplateResponse(
        request=request,
        name="orders_list.html",
        context={"orders": orders}
    )



@app.delete("/api/orders/{order_id}")
async def delete_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Сначала удаляем связанные контейнеры
    result = await db.execute(
        delete(Container).where(Container.order_id == order_id)
    )
    
    # 2. Теперь удаляем сам заказ
    result = await db.execute(
        delete(CargoOrder).where(
            CargoOrder.id == order_id, 
            CargoOrder.owner_id == current_user.id
        )
    )
    
    await db.commit()
    return HTMLResponse(status_code=200, content="")


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Каскадно отменяем все связанные контейнеры этого заказа
    container_query = (
        update(Container)
        .where(Container.order_id == order_id)
        .values(
            is_cancelled=True,
            cancelled_by_id=current_user.id,
            cancelled_at=datetime.utcnow(),
            cancel_reason="Удален заказчиком"
        )
    )
    await db.execute(container_query)
    
    # 2. Помечаем невалидным сам заказ
    order_query = (
        update(CargoOrder)
        .where(
            CargoOrder.id == order_id, 
            CargoOrder.owner_id == current_user.id,
            CargoOrder.is_valid == True
        )
        .values(is_valid=False)
    )
    
    result = await db.execute(order_query)
    
    # Если живой заказ для этого пользователя не найден — откатываем транзакцию
    if result.rowcount == 0:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Заказ не найден, уже отменен или у вас нет прав"
        )
        
    await db.commit()
    return HTMLResponse(status_code=200, content="")


@app.get("/api/orders/{order_id}/details")
async def get_order_details(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in [UserRole.ADMIN, UserRole.OPERATOR]:
        result = await db.execute(
            select(CargoOrder)
            .options(selectinload(CargoOrder.containers).selectinload(Container.equipment))
            .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
        )
    else:
        result = await db.execute(
            select(CargoOrder)
            .options(selectinload(CargoOrder.containers).joinedload(Container.equipment))
            .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
        )
    order = result.scalars().first()
    
    # Возвращаем маленький паршиал со списком
    return templates.TemplateResponse(
        request=request,
        name="partials/order_details_table.html",
        context={"order": order}
    )

@app.get("/api/orders/{order_id}/summary")
async def get_order_summary(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(CargoOrder)
        .options(
            # Подгружаем всё дерево связей для финала
            selectinload(CargoOrder.containers).options(
                selectinload(Container.items),
                selectinload(Container.equipment)
            ),
            selectinload(CargoOrder.port_of_loading),
            selectinload(CargoOrder.port_of_discharge),
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.equipment) # Общий тип из Шага 1
        )
        .where(CargoOrder.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    return templates.TemplateResponse(
        request=request,
        name="partials/order_summary.html",
        context={"order": order, "order_id": order_id}
    )

@app.post("/api/orders/{order_id}/confirm")
async def confirm_order(
    order_id: int,
    request: Request,
    background_tasks: BackgroundTasks,      # Магия фонового выполнения FastAPI
    #email_to: str = Form(...),              # Прилетает из формы Шага 4
    #email_text: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Находим заказ
    #result = await db.execute(
    #    select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    #)

    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.owner).joinedload(User.company),
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.port_of_loading),
            selectinload(CargoOrder.port_of_discharge),
            selectinload(CargoOrder.equipment),
            selectinload(CargoOrder.containers).options(
                selectinload(Container.equipment),
                selectinload(Container.items)
            )
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )

    order = result.scalars().first()

    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # Меняем статус
    order.status = "CONFIRMED"
    
    order.updated_at = datetime.now() 

    # Здесь можно добавить логику уведомления оператора (email или Telegram)
    
    

    # 2. Генерируем байты PDF, используя твою логику
    #html_content = templates.get_template("pdf/booking_note.html").render({"order": order})
    
    #pdf_buffer = BytesIO()
    #pisa_status = pisa.CreatePDF(BytesIO(html_content.encode("utf-8")), dest=pdf_buffer)

    #if pisa_status.err:
    #    raise HTTPException(status_code=500, detail="Ошибка конвертации HTML в PDF")
        
    #pdf_bytes = pdf_buffer.getvalue()

    await db.commit()

     # 3. Отправляем тяжелую задачу с SMTP в фон, чтобы бэкенд мгновенно освободился
    #background_tasks.add_task(
    #    send_email_worker,
    #    to_email='auto@n-l-n.ru',
    #    order_id=order_id,
    #   pdf_bytes=pdf_bytes,
    #   text_body='сгенерированная заявка'
    #

    # После подтверждения возвращаем пользователя в список заказов (Dashboard)
    # Используем HTMX-заголовок для перенаправления или просто отдаем список
    return templates.TemplateResponse(
        request=request,
        name="partials/order_success.html", # Финальное "Спасибо!"
        context={"order": order, "order_id": order_id}
    )

@app.get("/api/operator/dashboard")
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
                # Добавляем загрузку оборудования для отображения в списке
                selectinload(CargoOrder.containers).joinedload(Container.equipment),
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

@app.patch("/api/orders/containers/{container_id}/update-seal")
async def update_seal(
    container_id: int, 
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    form_data = await request.form()
    new_seal = form_data.get("seal")

    # Ищем контейнер и проверяем, что заказ принадлежит пользователю
    result = await db.execute(
        select(Container).join(CargoOrder).where(
            Container.id == container_id,
            CargoOrder.owner_id == current_user.id
        )
    )
    container = result.scalars().first()
    
    if not container:
        raise HTTPException(status_code=404)

    # Здесь МЫ НЕ ПРОВЕРЯЕМ статус заказа на DRAFT, 
    # так как пломбу можно вносить всегда до момента захода в порт
    container.seal = new_seal.strip().upper() if new_seal else None
    
    await db.commit()
    
    # Возвращаем "Ок" или просто обновленное значение, чтобы HTMX успокоился
    return Response(status_code=200)

@app.get("/api/operator/orders/{order_id}/manage")
async def manage_order(order_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    # Загружаем всё, включая данные по автодоставке и перевозчикам
    result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(CargoOrder.id == order_id)
    )
    order = result.scalars().first()
    
    return templates.TemplateResponse(
        request=request,
        name="operator/manage_order.html",
        context={"order": order, "user": getattr(request.state, "user", None)}
    )

@app.post("/api/operator/orders/{order_id}/update-ops")
async def update_order_ops(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    form_data = await request.form()
    ids = form_data.getlist("container_id[]")
    numbers = form_data.getlist("container_number[]")
    pins = form_data.getlist("pin_code[]")
    action = form_data.get("action") # save или confirm

    for i in range(len(ids)):
        con_id = int(ids[i])
        # Ищем конкретный контейнер
        result = await db.execute(select(Container).where(Container.id == con_id))
        container = result.scalar_one()
        
        # Обновляем только если это COC (номера и пины) отключил пока
        #if not container.is_soc:
        #    # Номера могут приходить в меньшем количестве, если часть полей была readonly
            # Но мы используем скрытые поля или фиксированные индексы
        #    if i < len(numbers):
        #        container.container_number = numbers[i]
        #        container.valid_number = validate_container_number(numbers[i])

        #    if i < len(pins): container.pin_code = pins[i]
        
        # Обновляем

        if i < len(numbers):
            container.container_number = numbers[i]
            container.valid_number = validate_container_number(numbers[i])

        if i < len(pins): container.pin_code = pins[i]

    # Если нажата кнопка "Подтвердить и запустить"
    if action == "confirm":
        result = await db.execute(select(CargoOrder).where(CargoOrder.id == order_id))
        order = result.scalar_one()
        order.status = "IN_PROGRESS"

    await db.commit()
    
    # Редирект обратно в дашборд оператора
    #return Response(headers={"HX-Redirect": "/api/operator/dashboard"})
    return await operator_dashboard(request, db)


@app.patch("/api/operator/containers/{container_id}/cancel")
async def cancel_container(
    container_id: int, 
    request: Request,
    reason: str = Form(None), # Получаем из hx-include
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    res = await db.execute(select(Container).where(Container.id == container_id))
    con = res.scalars().first()
    
    if con:
        con.is_cancelled = True
        con.cancel_reason = reason
        con.cancelled_at = datetime.now()
        con.cancelled_by_id = current_user.id
        await db.commit()
    
    # Перерисовываем всю страницу, как и раньше
    return await manage_order(request=request, order_id=con.order_id, db=db)


@app.get("/api/operator/containers/{container_id}/cancel-form")
async def get_cancel_form(container_id: int):
    return HTMLResponse(content=f"""
        <div class="flex flex-col gap-1">
            <input type="text" name="reason" id="reason-{container_id}" 
                   placeholder="Причина..." 
                   class="text-[10px] border border-red-300 rounded p-1 w-32 focus:outline-none">
            <div class="flex gap-2 justify-center">
                <button hx-patch="/api/operator/containers/{container_id}/cancel"
                        hx-include="#reason-{container_id}"
                        hx-target="#main-content"
                        class="text-[10px] bg-red-600 text-white px-2 py-0.5 rounded">OK</button>
                <button hx-get="/api/operator/dashboard" hx-target="#main-content" 
                        class="text-[10px] text-gray-500">Отмена</button>
            </div>
        </div>
    """)


@app.get("/api/orders/{order_id}/pdf")
async def generate_pdf(
    order_id: int, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Загружаем данные (как для Summary)
    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.owner).joinedload(User.company), # Данные вашей компании
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.port_of_loading),
            selectinload(CargoOrder.port_of_discharge),
            selectinload(CargoOrder.equipment),
            selectinload(CargoOrder.containers).options(
                selectinload(Container.equipment),
                selectinload(Container.items)  # Те самые CargoItems
            )
        )
        .where(CargoOrder.id == order_id)
    )
    order = result.scalars().first()

    # 2. Рендерим HTML через Jinja
    html_content = templates.get_template("pdf/booking_note.html").render({"order": order})

    # 3. Конвертируем HTML в PDF
    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_content.encode("utf-8")), dest=pdf_buffer)

    if pisa_status.err:
        return Response(content="Ошибка генерации PDF", status_code=500)

    # 4. Отдаем файл браузеру
    pdf_buffer.seek(0)
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=Booking_Note_{order_id}.pdf"
        }
    )

def build_pdf_bytes(order, template_env) -> bytes:
    """Принимает объект заказа и Jinja-окружение, возвращает чистые байты PDF"""
    # Твой оригинальный рендеринг и кодирование в utf-8
    html_content = template_env.get_template("pdf/booking_note.html").render({"order": order})
    
    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_content.encode("utf-8")), dest=pdf_buffer)

    if pisa_status.err:
        raise HTTPException(status_code=500, detail="Ошибка конвертации HTML в PDF")
        
    return pdf_buffer.getvalue()

def send_email_worker(to_email: str, order_id: int, pdf_bytes: bytes, text_body: str):
    """Этот воркер теперь полностью изолирован от БД и не вызовет MissingGreenlet"""
    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = f"Заявка на контейнерную перевозку №{order_id}"

    msg.attach(MIMEText(text_body, "plain", "utf-8"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes) # Просто вставляем готовые байты из памяти
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f"attachment; filename=Booking_Note_{order_id}.pdf",
    )
    msg.attach(part)


    try:
        # 1. Подключаемся к 25 порту в режиме чистого текста
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15.0) as server:
            server.ehlo()  # Приветствуем сервер
            
            # 2. Передаем логин и пароль (авторизация без TLS)
            server.login(SMTP_USER, SMTP_PASSWORD)
            
            # 3. Отправляем письмо
            server.sendmail(SMTP_USER, to_email, msg.as_string())
            print(f" Mail successfully sent with AUTH to {to_email}")
            
    except Exception as e:
        print(f"❌ Corporate SMTP Auth Error: {e}")