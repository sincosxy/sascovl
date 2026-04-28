from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Form
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.db import engine, Base, get_db
from sqlalchemy import select, delete  
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from app.models import User, Counterparty, CargoOrder, UserRole, Port, TransportType, Equipment, Container
from app.auth import verify_password, create_access_token, get_current_user, hash_password 
from app.schemas import Token, UserLogin, CounterpartyCreate, CounterpartyRead, OrderRead, UserCreate
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import List
from datetime import datetime

templates = Jinja2Templates(directory="app/templates")


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
        raise HTTPException(status_code=401, detail="Error")
    
    token = create_access_token(data={"sub": user.email, "role": user.role.value})
    
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=token,  path="/")
    return response

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)): #current_user: User = Depends(get_current_user)):
    try:
        current_user = await get_current_user(request, db)
        if current_user.role != UserRole.ADMIN:
            return RedirectResponse(url="/", status_code=303)

        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={"user": current_user}
        )
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    

@app.post("/api/admin/create-user")
async def admin_create_user(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(None),
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
        role=UserRole.USER
    )
    db.add(new_user)
    await db.commit()
    
    return HTMLResponse(content=f"<p class='text-green-600 font-bold'>Пользователь {email} создан!</p>")


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
    print(cps)
    options = "".join([f'<option value="{cp.name}">' for cp in cps])
    return HTMLResponse(content=options)
    #query = select(Counterparty).where(
    #    Counterparty.user_id == current_user.id,
    #    Counterparty.name.ilike(f"%{q}%")
    #).limit(5) # Отдаем топ-5 совпадений
    #result = await db.execute(query)
    #return result.scalars().all()

@app.post("/api/orders2")
async def create_order(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Теперь CargoOrder будет виден благодаря импорту выше
    new_order = CargoOrder(owner_id=current_user.id, status="draft")
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    # Возвращаем пока JSON для проверки, что 404 и NameError ушли
    #return {"id": new_order.id, "status": new_order.status}
    return templates.TemplateResponse(
        request=request, 
        name="partials/step_1.html", 
        context={"order_id": new_order.id}
    )

@app.post("/api/orders")
async def start_order(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Создаем пустой заказ (черновик)
    new_order = CargoOrder(owner_id=current_user.id, transport_type="CONTAINER")
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)

    # 2. Сразу вызываем функцию отрисовки первого шага
    # Важно: hx-target в кнопке index.html был #wizard-container, 
    # убедись что в step_1.html форма тоже будет работать внутри него
    return templates.TemplateResponse(
        request=request,
        name="partials/step_1.html",
        context={"order_id": new_order.id, "order": new_order,}
    )

@app.patch("/api/orders/{order_id}/step-1")
async def save_step_1(
    order_id: int,
    request: Request,
    transport_type: str = Form(...), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Ищем заказ СРАЗУ подгружая контрагентов для Шага 2
    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.shipper),
            selectinload(CargoOrder.consignee),
            selectinload(CargoOrder.notify_party)
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(status_code=404)

    # 2. Обновляем тип
    order.transport_type = transport_type.upper()
    await db.commit()
    await db.refresh(order)

    # 3. Достаем порты
    ports_result = await db.execute(select(Port).where(Port.is_active == True))
    ports = ports_result.scalars().all()

    # 4. Возвращаем ответ
    return templates.TemplateResponse(
        request=request,
        name="partials/step_2.html",
        context={
            "order": order, # Теперь shipper/consignee внутри него загружены
            "order_id": order_id, 
            "ports": ports
        }
    )

# Вспомогательная функция для "умного" поиска/создания
async def get_or_create_counterparty(name: str, user_id: int, db: AsyncSession):
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
    if not cp:
        # Создаем нового, если не нашли
        cp = Counterparty(name=name, user_id=user_id)
        db.add(cp)
        await db.flush() # Получаем ID без коммита всей транзакции
    return cp.id

@app.patch("/api/orders/{order_id}/step-2")
async def save_step_2(
    order_id: int,
    request: Request,
    pol_id: int = Form(...),
    pod_id: int = Form(...),
    shipper_name: str = Form(...),
    consignee_name: str = Form(...),
    notify_name: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Ищем заказ сразу с подгрузкой контейнеров для Шага 3
    result = await db.execute(
        select(CargoOrder)
        .options(selectinload(CargoOrder.containers)) 
        .where(
            CargoOrder.id == order_id, 
            CargoOrder.owner_id == current_user.id
        )
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404)

    # 2. Обрабатываем контрагентов
    order.shipper_id = await get_or_create_counterparty(shipper_name, current_user.id, db)
    order.consignee_id = await get_or_create_counterparty(consignee_name, current_user.id, db)
    order.notify_party_id = await get_or_create_counterparty(notify_name, current_user.id, db)

    # 3. Сохраняем порты
    order.pol_id = pol_id
    order.pod_id = pod_id

    # 4. Сохраняем и обновляем, чтобы объект был "свежим" для шаблона
    await db.commit()
    await db.refresh(order)

    # Определяем шаблон
    template_name = "partials/step_3_container.html" if order.transport_type == TransportType.CONTAINER else "partials/step_3_general.html"
    
    # Для контейнеров подтягиваем справочник оборудования
    equipments = []
    if order.transport_type == TransportType.CONTAINER:
        eq_result = await db.execute(select(Equipment))
        equipments = eq_result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "order_id": order_id, 
            "order": order,       # Теперь внутри загружены containers
            "equipments": equipments
        }
    )

@app.get("/api/orders/{order_id}/add-container-row")
async def add_container_row(request: Request, index: int = 0, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Теперь index берется из JS-параметра кнопки
    eq_res = await db.execute(select(Equipment))
    equipments = eq_res.scalars().all()
    
    return templates.TemplateResponse(
        request=request,
        name="partials/container_row.html", 
        context={
            "equipments": equipments, 
            "loop_index": index, # Передаем полученный индекс
            "container": None
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
    
    result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading), 
            joinedload(CargoOrder.port_of_discharge),
            joinedload(CargoOrder.containers)
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")


    eq_ids = form_data.getlist("equipment_id[]")
    numbers = form_data.getlist("container_number[]")
    seals = form_data.getlist("seal[]")
    weights = form_data.getlist("weight_gross[]")
    pieces = form_data.getlist("pieces[]")
    descriptions = form_data.getlist("cargo_description[]")
    
    soc_indices = form_data.getlist("is_soc[]")
    vent_indices = form_data.getlist("ventilation[]")
    port_indices = form_data.getlist("port_plug[]")
    vessel_indices = form_data.getlist("vessel_plug[]")
    
    temps = form_data.getlist("temperature[]")
    plug_dates = form_data.getlist("plug_start_date[]")

    await db.execute(delete(Container).where(Container.order_id == order_id))

    for i in range(len(eq_ids)):
        row_marker = str(i)
        
        def get_val(lst, idx, default=None):
            return lst[idx] if idx < len(lst) else default

        new_con = Container(
            order_id=order_id,
            equipment_id=int(eq_ids[i]),
            is_soc=row_marker in soc_indices,
            container_number=get_val(numbers, i),
            seal=get_val(seals, i),
            weight_gross=float(get_val(weights, i)) if get_val(weights, i) else 0.0,
            pieces=int(get_val(pieces, i)) if get_val(pieces, i) else 0,
            cargo_description=get_val(descriptions, i),
            
            # Реф-поля
            temperature=float(get_val(temps, i)) if get_val(temps, i) else None,
            ventilation=row_marker in vent_indices,
            port_plug=row_marker in port_indices,
            vessel_plug=row_marker in vessel_indices,
        )
        
        # Обработка даты
        date_str = get_val(plug_dates, i)
        if date_str:
            try:
                new_con.plug_start_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                new_con.plug_start_date = None

        db.add(new_con)

    await db.commit()
    
    # Освежаем объект для корректного отображения в success-шаблоне
    await db.refresh(order)

    return templates.TemplateResponse(
        request=request,
        name="partials/step_4_trucking.html",#"partials/order_success.html",
        context={"order_id": order_id, "order": order}
    )

@app.patch("/api/orders/{order_id}/step-4")
async def save_step_4(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    form_data = await request.form()
    result = await db.execute(
        select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()

    # Помощник для парсинга даты
    def parse_dt(dt_str):
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M") if dt_str else None

    # Сохранение Pre-carriage (Экспорт)
    order.pre_carriage_required = "pre_carriage_required" in form_data
    if order.pre_carriage_required:
        order.pre_carriage_address = form_data.get("pre_carriage_address")
        order.pre_carriage_contact = form_data.get("pre_carriage_contact")
        order.pre_carriage_carrier = None
    else:
        order.pre_carriage_address = None
        order.pre_carriage_contact = None
        order.pre_carriage_carrier = form_data.get("pre_carriage_carrier")
    order.pre_carriage_date = parse_dt(form_data.get("pre_carriage_date"))
    order.pre_carriage_comment = form_data.get("pre_carriage_comment")

    # Сохранение On-carriage (Импорт)
    order.on_carriage_required = "on_carriage_required" in form_data
    order.on_carriage_address = form_data.get("on_carriage_address")
    order.on_carriage_contact = form_data.get("on_carriage_contact")
    order.on_carriage_date = parse_dt(form_data.get("on_carriage_date"))
    order.on_carriage_comment = form_data.get("on_carriage_comment")

    await db.commit()

    result = await db.execute(
        select(CargoOrder)
        .options(
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            joinedload(CargoOrder.shipper),
            joinedload(CargoOrder.consignee),
            # Важно для таблицы контейнеров:
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(CargoOrder.id == order_id)
    )
    order = result.scalars().first()

    return templates.TemplateResponse(
        request=request,
        name="partials/order_summary.html", # Или тот шаблон, который ты вызываешь
        context={
            "order": order, 
            "order_id": order_id
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
            joinedload(CargoOrder.port_of_loading), # или как оно у тебя в моделях?
            joinedload(CargoOrder.port_of_discharge),
            #joinedload(CargoOrder.shipper),
            #joinedload(CargoOrder.consignee)
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(CargoOrder.owner_id == current_user.id)
        .order_by(CargoOrder.id.desc())
    )
    orders = result.scalars().all()
    
    return templates.TemplateResponse(
        request=request,
        name="orders_list.html",
        context={"orders": orders}
    )

@app.get("/api/orders/{order_id}/step-1")
async def get_step_1(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id))
    order = result.scalars().first()
    if not order: raise HTTPException(404)

    return templates.TemplateResponse(
        request=request,
        name="partials/step_1.html",
        context={"order": order, "order_id": order_id}
    )

# GET Шаг 2: Маршрут и Контрагенты
@app.get("/api/orders/{order_id}/step-2")
async def get_step_2(order_id: int, request: Request, db: AsyncSession = Depends(get_db)):
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
    
    ports_res = await db.execute(select(Port).where(Port.is_active == True))
    ports = ports_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_2.html",
        context={"order": order, "order_id": order_id, "ports": ports}
    )


@app.get("/api/orders/{order_id}/step-3")
async def get_step_3(
    order_id: int, 
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Добавляем цепочку загрузки: Заказ -> Контейнеры -> Тип оборудования
    result = await db.execute(
        select(CargoOrder)
        .options(
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    eq_res = await db.execute(select(Equipment))
    equipments = eq_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="partials/step_3_container.html",
        context={
            "order": order, 
            "order_id": order_id, 
            "equipments": equipments
        }
    )

@app.get("/api/orders/{order_id}/step-4")
async def get_step_4(
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
        raise HTTPException(status_code=404, detail="Заказ не найден")

    return templates.TemplateResponse(
        request=request,
        name="partials/step_4_trucking.html", 
        context={"order": order, "order_id": order_id}
    )


@app.delete("/api/orders/{order_id}")
async def delete_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 1. Сначала удаляем связанные контейнеры
    await db.execute(
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


@app.get("/api/orders/{order_id}/details")
async def get_order_details(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
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
            joinedload(CargoOrder.port_of_loading),
            joinedload(CargoOrder.port_of_discharge),
            joinedload(CargoOrder.shipper),
            joinedload(CargoOrder.consignee),
            joinedload(CargoOrder.notify_party),  # Добавь, если используешь его в шаблоне
            # Загружаем контейнеры И их тип оборудования (Equipment)
            selectinload(CargoOrder.containers).joinedload(Container.equipment)
        )
        .where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Находим заказ
    result = await db.execute(
        select(CargoOrder).where(CargoOrder.id == order_id, CargoOrder.owner_id == current_user.id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # Меняем статус
    order.status = "CONFIRMED"
    
    # Здесь можно добавить логику уведомления оператора (email или Telegram)
    
    await db.commit()

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
                selectinload(CargoOrder.containers).joinedload(Container.equipment)
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
        
        # Обновляем только если это COC (номера и пины)
        if not container.is_soc:
            # Номера могут приходить в меньшем количестве, если часть полей была readonly
            # Но мы используем скрытые поля или фиксированные индексы
            if i < len(numbers): container.container_number = numbers[i]
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