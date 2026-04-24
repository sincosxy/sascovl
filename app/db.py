from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings # Мы его уже создали, пусть живет в core

engine = create_async_engine(settings.DATABASE_URL)
SessionLocal = async_sessionmaker(autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with SessionLocal() as session:
        yield session
