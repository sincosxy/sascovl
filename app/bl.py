"""Постановка контейнеров в рейс и нумерация коносаментов.

Формат номера коносамента:  Кор 0123 V 001 K
    Кор   — линия (Кор / Маг / Пет: Корсаковская, Магаданская, Петропавловск-Камчатская)
    0123  — 4 цифры номера рейса
    V     — первая буква порта отправления (V, M, K, P)
    001   — порядковый номер коносамента в рейсе
    K     — первая буква порта назначения
"""
import re
from datetime import date, datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.models import CargoOrder, Container, Notification, Port, User, UserRole, Voyage

# (подстроки названия порта, латинская буква порта, префикс линии)
_PORTS = (
    (("владивосток", "vladivostok"), "V", None),
    (("корсаков", "korsakov"), "K", "Кор"),
    (("магадан", "magadan"), "M", "Маг"),
    (("петропавловск", "petropavlovsk"), "P", "Пет"),
)

STATUS_IN_PROGRESS = "IN_PROGRESS"   # «В работе»: утверждена оператором
STATUS_EXECUTING = "EXECUTING"       # «Выполняется»: все контейнеры распланированы по рейсам

BL_MODE_PER_CONTAINER = "per_container"
BL_MODE_PER_ORDER = "per_order"


def _match_port(port: Optional[Port]):
    name = (port.name or "").lower() if port else ""
    for needles, letter, line in _PORTS:
        if any(n in name for n in needles):
            return letter, line
    return None, None


def port_letter(port: Optional[Port]) -> Optional[str]:
    return _match_port(port)[0]


def bl_prefix(voyage: Voyage) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Возвращает (префикс до порядкового номера, буква порта назначения, ошибка).

    Линия определяется по порту, который не Владивосток; если оба порта
    «линейные» (например, Магадан → Петропавловск) — по порту назначения.
    """
    pol_letter, pol_line = _match_port(voyage.departure_port)
    pod_letter, pod_line = _match_port(voyage.destination_port)

    if not pol_letter or not pod_letter:
        unknown = voyage.departure_port if not pol_letter else voyage.destination_port
        return None, None, f"порт «{unknown.name if unknown else '—'}» не входит в схему нумерации (V, M, K, P)"

    line = pod_line or pol_line
    if not line:
        return None, None, "не удалось определить линию: оба порта рейса — Владивосток"

    digits = re.sub(r"\D", "", voyage.number or "")
    if not digits:
        return None, None, f"в номере рейса «{voyage.number}» нет цифр"
    number4 = digits[-4:].zfill(4)

    return f"{line}{number4}{pol_letter}", pod_letter, None


def _next_sequence(prefix: str, pod_letter: str, existing: Iterable[Optional[str]]) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{3,}}){re.escape(pod_letter)}$")
    used = [int(m.group(1)) for bl in existing if bl and (m := pattern.match(bl))]
    return max(used, default=0) + 1


async def sync_order_status(db: AsyncSession, order_ids: Iterable[Optional[int]]) -> None:
    """Переключает заявку между «В работе» и «Выполняется».

    «Выполняется» — когда у заявки есть хотя бы один неотменённый контейнер и все
    неотменённые контейнеры стоят в рейсах. Если условие перестало выполняться
    (контейнер сняли с рейса, восстановили отменённый) — обратно «В работе».
    Другие статусы (черновик, новая заявка) не трогает. Вызывать до commit.
    """
    ids = {i for i in order_ids if i}
    if not ids:
        return
    await db.flush()  # у сессии autoflush выключен — без этого запрос не увидит свежие изменения

    result = await db.execute(
        select(CargoOrder).where(
            CargoOrder.id.in_(ids),
            CargoOrder.status.in_([STATUS_IN_PROGRESS, STATUS_EXECUTING]),
        )
    )
    orders = result.scalars().all()
    if not orders:
        return

    result = await db.execute(
        select(Container.order_id, Container.voyage_id).where(
            Container.order_id.in_([o.id for o in orders]),
            Container.is_cancelled.isnot(True),
        )
    )
    active: dict[int, list] = {}
    for order_id, voyage_id in result.all():
        active.setdefault(order_id, []).append(voyage_id)

    for order in orders:
        voyages = active.get(order.id, [])
        all_planned = bool(voyages) and all(v is not None for v in voyages)
        order.status = STATUS_EXECUTING if all_planned else STATUS_IN_PROGRESS


def voyage_title(voyage: Voyage) -> str:
    """«0123 (т/х Сахалин, отход 15.11.2026)» — для текстов уведомлений."""
    details = []
    if voyage.vessel:
        details.append(f"т/х {voyage.vessel.name}")
    if voyage.departure_date:
        details.append(f"отход {voyage.departure_date.strftime('%d.%m.%Y')}")
    return f"{voyage.number} ({', '.join(details)})" if details else f"{voyage.number}"


def notify_owner(db: AsyncSession, order: Optional[CargoOrder], text: str) -> None:
    """Кладёт уведомление владельцу заявки. Сохранится вместе с ближайшим commit."""
    if order and order.owner_id:
        db.add(Notification(user_id=order.owner_id, order_id=order.id, text=text))


def local_today() -> date:
    """Сегодняшняя дата по Владивостоку (сервер может жить в UTC)."""
    return datetime.now(ZoneInfo("Asia/Vladivostok")).date()


def container_label(con: Container) -> str:
    return (con.container_number or "").strip() or f"ID {con.id}"


async def notify_staff(db: AsyncSession, order: Optional[CargoOrder], text: str) -> None:
    """Кладёт уведомление всем активным операторам и администраторам. Сохранится с ближайшим commit."""
    result = await db.execute(
        select(User.id).where(
            User.role.in_([UserRole.OPERATOR, UserRole.ADMIN]),
            User.is_active.isnot(False),
        )
    )
    for user_id in result.scalars().all():
        db.add(Notification(user_id=user_id, order_id=order.id if order else None, text=text))


def _seal_notice_prefix(con: Container, order: CargoOrder) -> str:
    return f"Пломба контейнера {container_label(con)} по заявке №{order.id}:"


async def notify_staff_about_seal(db: AsyncSession, con: Container, order: CargoOrder) -> None:
    """Клиент указал/изменил пломбу — операторам пора ждать контейнер на терминале.

    Непрочитанное уведомление о пломбе этого же контейнера заменяется свежим,
    чтобы правки пломбы не плодили строки.
    """
    prefix = _seal_notice_prefix(con, order)
    await db.execute(
        delete(Notification).where(
            Notification.order_id == order.id,
            Notification.is_read == False,
            Notification.text.startswith(prefix, autoescape=True),
        )
    )
    await notify_staff(
        db, order,
        f"{prefix} {con.seal}. Клиент указал пломбу — подтвердите поступление контейнера на терминал."
    )


def can_confirm_arrival(con: Container) -> Optional[str]:
    """Почему нельзя подтвердить поступление (или None, если можно)."""
    order = con.order
    if con.is_cancelled:
        return "отменён"
    if con.arrived_at:
        return f"уже принят {con.arrived_at.strftime('%d.%m.%Y')}"
    if not order or not order.is_valid or order.status == "draft":
        return "заявка не подтверждена или отменена"
    if not (con.seal or "").strip():
        return "клиент ещё не указал пломбу"
    return None


async def confirm_arrival(
    db: AsyncSession,
    container_ids: list[int],
    arrived_at: Optional[date],
    user_id: Optional[int],
) -> dict:
    """Подтверждает поступление контейнеров на терминал. Коммит делает сама.

    Возвращает {"arrived": n, "skipped": [...], "warnings": [], "error": str|None}.
    """
    report = {"arrived": 0, "skipped": [], "warnings": [], "error": None}
    if not container_ids:
        report["error"] = "Не выбрано ни одного контейнера"
        return report
    arrived_at = arrived_at or local_today()
    if arrived_at > local_today():
        report["error"] = "Дата поступления не может быть в будущем"
        return report

    result = await db.execute(
        select(Container)
        .options(joinedload(Container.order))
        .where(Container.id.in_(container_ids))
        .order_by(Container.order_id, Container.id)
    )
    for con in result.scalars().all():
        reason = can_confirm_arrival(con)
        if reason:
            report["skipped"].append(f"{container_label(con)}: {reason}")
            continue
        con.arrived_at = arrived_at
        con.arrival_confirmed_by_id = user_id
        report["arrived"] += 1
        notify_owner(
            db, con.order,
            f"Контейнер {container_label(con)} по вашей заявке №{con.order.id} "
            f"принят на терминал {arrived_at.strftime('%d.%m.%Y')}."
        )

    await db.commit()
    return report


async def cancel_arrival(db: AsyncSession, container_id: int) -> Optional[str]:
    """Отменяет подтверждение поступления (ошиблись). Нельзя, если контейнер уже в рейсе."""
    result = await db.execute(
        select(Container).options(joinedload(Container.order)).where(Container.id == container_id)
    )
    con = result.scalars().first()
    if not con:
        return "Контейнер не найден"
    if con.voyage_id is not None:
        return "Контейнер уже в рейсе — сначала снимите его с рейса"
    if con.arrived_at:
        notify_owner(
            db, con.order,
            f"Отметка о поступлении контейнера {container_label(con)} по вашей заявке №{con.order_id} "
            "на терминал отменена оператором."
        )
    con.arrived_at = None
    con.arrival_confirmed_by_id = None
    await db.commit()
    return None


def missing_for_voyage(con: Container) -> list[str]:
    """Чего не хватает контейнеру для постановки в рейс: номер, пломба и поступление на терминал."""
    missing = []
    if not (con.container_number or "").strip():
        missing.append("номер контейнера")
    if not (con.seal or "").strip():
        missing.append("пломба")
    if not con.arrived_at:
        missing.append("поступление на терминал")
    return missing


async def assign_containers_to_voyage(
    db: AsyncSession,
    voyage_id: int,
    container_ids: list[int],
    bl_mode: str = BL_MODE_PER_CONTAINER,
) -> dict:
    """Ставит контейнеры в рейс и присваивает им номера коносаментов.

    Возвращает {"assigned": n, "skipped": [...], "warnings": [...], "error": str|None}.
    Коммит делает сама.
    """
    report = {"assigned": 0, "skipped": [], "warnings": [], "error": None}

    # Блокируем строку рейса: порядковые номера коносаментов выдаются по очереди
    await db.execute(select(Voyage.id).where(Voyage.id == voyage_id).with_for_update())
    result = await db.execute(
        select(Voyage)
        .options(
            joinedload(Voyage.departure_port),
            joinedload(Voyage.destination_port),
            joinedload(Voyage.vessel),
        )
        .where(Voyage.id == voyage_id)
    )
    voyage = result.scalars().first()
    if not voyage:
        report["error"] = "Рейс не найден"
        return report
    if not container_ids:
        report["error"] = "Не выбрано ни одного контейнера"
        return report

    result = await db.execute(
        select(Container)
        .options(joinedload(Container.order))
        .where(Container.id.in_(container_ids))
        .order_by(Container.order_id, Container.id)
    )
    containers = result.scalars().all()

    result = await db.execute(
        select(Container.order_id, Container.bl_number).where(Container.voyage_id == voyage_id)
    )
    in_voyage = result.all()

    prefix, pod_letter, prefix_error = bl_prefix(voyage)
    if prefix_error:
        report["warnings"].append(
            f"Номера коносаментов не сформированы: {prefix_error}. Их можно ввести вручную."
        )
    next_seq = _next_sequence(prefix, pod_letter, (bl for _, bl in in_voyage)) if prefix else None

    # Для режима «один коносамент на заявку»: уже выданные в этом рейсе номера
    order_bl = {order_id: bl for order_id, bl in in_voyage if bl and order_id}

    route_mismatch = 0
    for con in containers:
        label = (con.container_number or "").strip() or f"ID {con.id}"
        order = con.order
        if con.is_cancelled:
            report["skipped"].append(f"{label}: отменён")
            continue
        if con.voyage_id is not None:
            report["skipped"].append(f"{label}: уже стоит в рейсе")
            continue
        if not order or not order.is_valid or order.status == "draft":
            report["skipped"].append(f"{label}: заявка не подтверждена или отменена")
            continue

        missing = missing_for_voyage(con)
        if missing:
            report["skipped"].append(f"{label}: не хватает — {', '.join(missing)}")
            continue

        if order.pol_id != voyage.departure_port_id or order.pod_id != voyage.destination_port_id:
            route_mismatch += 1

        con.voyage_id = voyage.id
        if prefix:
            if bl_mode == BL_MODE_PER_ORDER and con.order_id in order_bl:
                con.bl_number = order_bl[con.order_id]
            else:
                con.bl_number = f"{prefix}{next_seq:03d}{pod_letter}"
                next_seq += 1
                if bl_mode == BL_MODE_PER_ORDER:
                    order_bl[con.order_id] = con.bl_number
        report["assigned"] += 1
        notify_owner(
            db, order,
            f"Контейнер {label} по вашей заявке №{order.id} назначен на рейс {voyage_title(voyage)}. "
            + (f"Номер коносамента: {con.bl_number}." if con.bl_number else "Номер коносамента будет присвоен позже.")
        )

    if route_mismatch:
        report["warnings"].append(
            f"У {route_mismatch} конт. маршрут заявки не совпадает с маршрутом рейса — проверьте."
        )

    await sync_order_status(db, {con.order_id for con in containers if con.voyage_id == voyage.id})
    await db.commit()
    return report


async def remove_container_from_voyage(db: AsyncSession, voyage_id: int, container_id: int) -> bool:
    result = await db.execute(
        select(Container)
        .options(joinedload(Container.order), joinedload(Container.voyage))
        .where(Container.id == container_id, Container.voyage_id == voyage_id)
    )
    con = result.scalars().first()
    if not con:
        return False
    label = (con.container_number or "").strip() or f"ID {con.id}"
    if con.order:
        notify_owner(
            db, con.order,
            f"Контейнер {label} по вашей заявке №{con.order.id} снят с рейса {con.voyage.number}. "
            "О новом рейсе сообщим дополнительно."
        )
    con.voyage_id = None
    con.bl_number = None
    await sync_order_status(db, [con.order_id])
    await db.commit()
    return True
