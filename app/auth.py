import bcrypt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from jose import jwt, JWTError
from app.config import settings
from app.db import get_db
from app.models import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

async def get_current_user(
        #token: str = Depends(oauth2_scheme),
        request: Request,
        db: AsyncSession = Depends(get_db),

) -> User:
    token = request.cookies.get("access_token")

    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    
    # HX-Redirect: HTMX-запрос с просроченным/поддельным токеном уводит на страницу входа
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer", "HX-Redirect": "/login"},
    )

    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    # Деактивированный пользователь теряет доступ сразу, а не когда истечёт токен
    if user is None or not user.is_active:
        raise credentials_exception
    return user


STAFF_ROLES = (UserRole.ADMIN, UserRole.OPERATOR)


async def require_staff(user: User = Depends(get_current_user)) -> User:
    """Оператор или администратор. Клиента (USER) отправляет на главную."""
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Доступ запрещен", headers={"HX-Redirect": "/"})
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Только администратор."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    return user


def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode('utf-8'), 
        hashed_password.encode('utf-8')
    )

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt
