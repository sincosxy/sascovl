from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Float, Boolean, Date, DateTime
from sqlalchemy.orm import relationship
from app.db import Base
import enum
from sqlalchemy.sql import func

class UserRole(enum.Enum):
    USER = "user"
    ADMIN = "admin"
    OPERATOR = "operator"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String, unique=False, nullable=True)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.USER)
    orders = relationship("CargoOrder", back_populates="owner")
    company = relationship("Company")
    is_active = Column(Boolean, default=True)

class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    fullname = Column(String, index=True)
    inn = Column(String, unique=True, index=True, nullable=True) # ИНН может не быть у иноземцев
    kpp = Column(String, unique=False, nullable=True)
    ogrn = Column(String, unique=False, nullable=True)
    address1 = Column(String, unique=False, nullable=True)
    address2 = Column(String, unique=False, nullable=True)
    tel1 = Column(String, unique=False, nullable=True)
    tel2 = Column(String, unique=False, nullable=True)
    email1 = Column(String, unique=False, nullable=True)
    email2 = Column(String, unique=False, nullable=True)
    bik = Column(String, unique=False, nullable=True)
    ks = Column(String, unique=False, nullable=True)
    rs = Column(String, unique=False, nullable=True)
    is_deleted = Column(Boolean, default=False)



class TransportType(enum.Enum):
    CONTAINER = "container"
    GENERAL_CARGO = "general_cargo"

class Port(Base):
    __tablename__ = "ports"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    country = Column(String)
    code = Column(String, unique=True)
    is_active = Column(Boolean, default=True)

class Counterparty(Base):
    __tablename__ = "counterparties"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False, index=True)
    inn = Column(String, nullable=True)
    address = Column(String, nullable=True)
    contact_info = Column(String, nullable=True)
    last_use = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    use_count = Column(Integer, default=1)

    user = relationship("User")

class CargoOrder(Base):
    __tablename__ = "cargo_orders"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="draft")
    transport_type = Column(Enum(TransportType))
    loading_date = Column(Date, nullable=True)
    equipment_id = Column(Integer, ForeignKey("equipments.id"), nullable=True)
    is_soc = Column(Boolean, nullable=True, default=None)
    needs_return = Column(Boolean, nullable=True, default=None)
    return_instructions = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    shipper_id = Column(Integer, ForeignKey("counterparties.id"))
    consignee_id = Column(Integer, ForeignKey("counterparties.id"))
    notify_party_id = Column(Integer, ForeignKey("counterparties.id"))

    pol_id = Column(Integer, ForeignKey("ports.id"))
    pod_id = Column(Integer, ForeignKey("ports.id"))

        
    shipper = relationship("Counterparty", foreign_keys=[shipper_id])
    consignee = relationship("Counterparty", foreign_keys=[consignee_id])
    notify_party = relationship("Counterparty", foreign_keys=[notify_party_id])

    port_of_loading = relationship("Port", foreign_keys=[pol_id])
    port_of_discharge = relationship("Port", foreign_keys=[pod_id])
    owner = relationship("User", back_populates="orders")
    containers = relationship("Container", back_populates="order", cascade="all, delete-orphan")
    items = relationship("GeneralCargoItem", back_populates="order", cascade="all, delete-orphan")
    equipment = relationship("Equipment", back_populates="cargo_orders")

    # Pre-carriage (Экспорт / Пункт отправления)
    pre_carriage_required = Column(Boolean, default=False)
    pre_carriage_address = Column(String, nullable=True)
    pre_carriage_contact = Column(String, nullable=True)
    pre_carriage_date = Column(DateTime, nullable=True)
    pre_carriage_comment = Column(String, nullable=True)
    pre_carriage_carrier = Column(String, nullable=True)

    # On-carriage (Импорт / Пункт назначения)
    on_carriage_required = Column(Boolean, default=False)
    on_carriage_address = Column(String, nullable=True)
    on_carriage_contact = Column(String, nullable=True)
    on_carriage_notes = Column(String, nullable=True)
    on_carriage_comment = Column(String, nullable=True)



class Equipment(Base):
    __tablename__ = "equipments"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    description = Column(String, nullable=True)
    cargo_orders = relationship("CargoOrder", back_populates="equipment")

class Container(Base):
    __tablename__ = "containers"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("cargo_orders.id"))
    
    equipment_id = Column(Integer, ForeignKey("equipments.id"), nullable=False) # 20DC, 40HC и т.д.
    is_soc = Column(Boolean, default=False) # True - отправителя, False - линейный
    is_lcl = Column(Boolean, default=False)
    weight_gross = Column(Float, nullable=True)
    pieces = Column(Integer, nullable=True)
    cargo_description = Column(String)
    container_number = Column(String, nullable=True)
    valid_number = Column(Boolean, default=True)
    seal = Column(String, nullable=True)
    pin_code = Column(String, nullable=True)
    is_cancelled = Column(Boolean, default=False)
    cancelled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(String, nullable=True)

    # --- поля для Рефов ---
    temperature = Column(Float, nullable=True)
    ventilation = Column(Boolean, default=False)
    port_plug = Column(Boolean, default=False)
    vessel_plug = Column(Boolean, default=False)
    plug_start_date = Column(Date, nullable=True)

    order = relationship("CargoOrder", back_populates="containers")
    equipment = relationship("Equipment")
    items = relationship("CargoItem", back_populates="container", cascade="all, delete-orphan")

class CargoItem(Base):
    __tablename__ = "cargo_items"
    
    id = Column(Integer, primary_key=True)
    container_id = Column(Integer, ForeignKey("containers.id", ondelete="CASCADE"))
    
    name = Column(String) # Наименование груза
    pieces = Column(Integer) # Мест
    weight_gross = Column(Float) # Вес
    
    container = relationship("Container", back_populates="items")


class GeneralCargoItem(Base):
    __tablename__ = "general_cargo_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("cargo_orders.id"))
    
    name = Column(String)      # Наименование (напр. "Трубы")
    quantity = Column(Integer) # Кол-во мест
    weight_gross = Column(Float)
    volume = Column(Float)     # Объем
    dimensions = Column(String) # Габариты (ДхШхВ)

    order = relationship("CargoOrder", back_populates="items")

User.orders = relationship("CargoOrder", back_populates="owner")