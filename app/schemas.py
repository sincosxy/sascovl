from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional 
from datetime import datetime

class Token(BaseModel):
    access_token: str
    token_type: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    
class CounterpartyBase(BaseModel):
    name: str
    inn: Optional[str] = None
    address: Optional[str] = None
    contact_info: Optional[str] = None

class CounterpartyCreate(CounterpartyBase):
    pass

class CounterpartyRead(CounterpartyBase):
    id: int

    model_config = ConfigDict(from_attributes=True)

class OrderBase(BaseModel):
    cargo_type: Optional[str] = None
    weight: Optional[float] = None
    volume: Optional[float] = None
    origin_address: Optional[str] = None
    destination_address: Optional[str] = None

# Схема для создания (можно пустую)
class OrderCreate(OrderBase):
    pass

# Схема, которую вернет сервер (с ID и статусом)
class OrderRead(OrderBase):
    id: int
    status: str
    created_at: datetime
    owner_id: int

    class Config:
        from_attributes = True