import asyncio
import bcrypt # Импортируем напрямую
from app.db import SessionLocal 
from app.models import User, UserRole 
from app.config import settings
from sqlalchemy import select

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')

async def create_admin():
    async with SessionLocal() as db:
        admin_email = settings.PGADMIN_DEFAULT_EMAIL
        admin_pass = settings.PGADMIN_DEFAULT_PASSWORD 

        result = await db.execute(select(User).filter(User.email == admin_email))
        if result.scalars().first():
            print(f"Админ {admin_email} уже существует.")
            return

        # Хешируем твоим методом
        hashed = hash_password(admin_pass)

        new_admin = User(
            email=admin_email,
            hashed_password=hashed,
            role=UserRole.ADMIN
        )

        db.add(new_admin)
        await db.commit()
        print(f"--- УСПЕХ ---")
        print(f"Админ создан: {admin_email}")

if __name__ == "__main__":
    asyncio.run(create_admin())
