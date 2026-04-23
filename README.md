# CargoFlow — Личный кабинет заказчика (MVP)

## Суть проекта
Система для пошагового оформления заявок на перевозку грузов (Wizard form). 
Регистрация только через администратора.

## Структура проекта (Плоская)
- `app/main.py` — Точка входа FastAPI и роуты.
- `app/models.py` — Все таблицы БД (User, CargoOrder) + SQLAlchemy Base.
- `app/schemas.py` — Pydantic модели (валидация данных).
- `app/db.py` — Настройка асинхронного движка и сессий БД.
- `app/config.py` — Чтение .env через Pydantic Settings.
- `app/auth.py` — Логика безопасности (хеширование, JWT).
- `alembic/` — Миграции базы данных.

## Стек
- **Backend:** Python 3.10+, FastAPI, SQLAlchemy (Async), Alembic.
- **Database:** PostgreSQL (в Docker) + pgAdmin (порт 5050).
- **Environment:** WSL2, venv, .env (игнорируется гитом).

## Запуск инфраструктуры
1. База: `docker-compose up -d`
2. Миграции: `alembic upgrade head`
3. Создание админа: `python create_first_admin.py` (скрипт в корне)


# CargoFlow — Backend (Current State)

## Что реализовано:
1. **Инфраструктура:** 
   - PostgreSQL + pgAdmin в Docker.
   - Миграции через Alembic (настроены на асинхронный драйвер для проекта и синхронный для миграций).
2. **База данных:**
   - Таблицы `users` (с ролями user/admin) и `cargo_orders`.
3. **Безопасность:**
   - Хеширование паролей через `bcrypt` (прямое использование библиотеки).
   - JWT-авторизация (создание и валидация токенов).
   - Интеграция со Swagger (замочек авторизации через OAuth2PasswordRequestForm).
4. **Инструментарий:**
   - Скрипт `create_first_admin.py` для инициализации системы.

## Как запустить после клонирования:
1. Поднять базу: `docker-compose up -d`
2. Создать venv и поставить зависимости: `pip install -r requirements.txt`
3. Применить миграции: `alembic upgrade head`
4. Запустить сервер: `python -m uvicorn app.main:app --reload`

## Текущие эндпоинты:
- `POST /api/login` — получение токена (принимает Form Data).
- `POST /api/orders` — создание пустой заявки-черновика (требует авторизации).
- `GET /api/me` — проверка текущего пользователя.
