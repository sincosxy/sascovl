from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Float, Boolean
from sqlalchemy.orm import relationship
from app.db import Base
import enum

class UserRole(enum.Enum):
    USER = "user"
    ADMIN = "admin"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.USER)
    orders = relationship("CargoOrder", back_populates="owner")

class TransportType(enum.Enum):
    CONTAINER = "container"
    GENERAL_CARGO = "general_cargo"

class Port(Base):
    __tablename__ = "ports"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    country = Column(String)
    code = Column(String, unique=True)

class Counterparty(Base):
    __tablename__ = "counterparties"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False, index=True)
    inn = Column(String, nullable=True)
    address = Column(String, nullable=True)
    contact_info = Column(String, nullable=True)

    user = relationship("User")

class CargoOrder(Base):
    __tablename__ = "cargo_orders"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="draft")
    transport_type = Column(Enum(TransportType))

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

class Equipment(Base):
    __tablename__ = "equipments"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    description = Column(String, nullable=True)

class Container(Base):
    __tablename__ = "containers"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("cargo_orders.id"))
    
    equipment_id = Column(Integer, ForeignKey("equipments.id"), nullable=False) # 20DC, 40HC и т.д.
    is_soc = Column(Boolean, default=False) # True - отправителя, False - линейный
    weight_gross = Column(Float)
    cargo_description = Column(String)
    container_number = Column(String, nullable=True)
    seal = Column(String, nullable=True)

    order = relationship("CargoOrder", back_populates="containers")
    equipment = relationship("Equipment")

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