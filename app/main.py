from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm 
from app.db import engine, Base, get_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User
from app.auth import verify_password, create_access_token
from app.schemas import Token, UserLogin



app = FastAPI(title="CargoFlow API")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

@app.get("/")
async def root():
    return {"message": "CargoFlow API is running"}

@app.get("/me")
async def read_user_me(token: str = Depends(oauth2_scheme)):
    return {"token": token, "info": "This route is seen by lock only"}

@app.post("/api/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    #user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).filter(User.email == form_data.username))
    user = result.scalars().first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong email or password"
        )
    
    access_token = create_access_token(data={"sub": user.email, "role":user.role.value})
    return {"access_token": access_token, "token_type": "bearer"}

# Это пригодится чуть позже для подключения роутов
# from app.api.v1.api import api_router
# app.include_router(api_router, prefix="/api/v1")
