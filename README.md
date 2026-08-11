# MindSurve API

Backend API for **MindSurve**. Implemented: **authentication** and **projects / chats / messages**.

## Tech stack

- Python 3.11+
- FastAPI + Uvicorn
- PostgreSQL
- SQLAlchemy 2.x + Alembic
- Pydantic v2 / pydantic-settings
- PyJWT + bcrypt
- pytest + httpx

## Local setup

### 1. Virtual environment

```powershell
cd D:\TikunTech\mindsurve-ai-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. Environment

```powershell
copy .env.example .env
```

Set at least:

- `DATABASE_URL` — Postgres connection (`postgresql+psycopg://...`)
- `JWT_SECRET_KEY` — long random secret
- `FRONTEND_URL` — Next.js origin (default `http://localhost:3000`)

### 3. Database + migrations

```powershell
python scripts/ensure_db.py
alembic upgrade head
```

### 4. Run API

```powershell
uvicorn app.main:app --reload
```

- Health: `GET http://127.0.0.1:8000/health`
- Auth: `/api/v1/auth/*`

### 5. Tests

```powershell
pytest
```

Auth tests use an in-memory SQLite database and do not require Postgres.

## Auth endpoints

| Method | Path | Notes |
|--------|------|--------|
| POST | `/api/v1/auth/register` | Creates user + session; sets HttpOnly refresh cookie |
| POST | `/api/v1/auth/login` | Issues access token + refresh cookie |
| POST | `/api/v1/auth/refresh` | Rotates refresh cookie; returns new access token |
| POST | `/api/v1/auth/logout` | Revokes session; clears cookie |
| GET | `/api/v1/auth/me` | Bearer access token required |

Access tokens expire in 15 minutes. Refresh sessions last 30 days (configurable).

## Project & chat endpoints

| Method | Path | Notes |
|--------|------|--------|
| GET/POST | `/api/v1/projects` | List / create (title) |
| GET/PATCH/DELETE | `/api/v1/projects/{id}` | Get / rename / delete (cascades chats) |
| GET/POST | `/api/v1/projects/{id}/chats` | List / create chat |
| POST | `/api/v1/projects/{id}/chats/start` | Create chat + first user message |
| GET | `/api/v1/chats` | All chats for current user |
| GET/PATCH/DELETE | `/api/v1/chats/{id}` | Get / rename / delete |
| GET/POST | `/api/v1/chats/{id}/messages` | List / add message |

Apply new migrations yourself: `alembic upgrade head` (agents create migration files only).

## Project layout

```text
app/api/v1/auth.py     Auth routes
app/services/          Business logic
app/repositories/      Persistence
app/db/models/         SQLAlchemy models
app/schemas/           Pydantic schemas
alembic/versions/      Migrations
tests/                 pytest suite
```

See `AGENTS.md` for engineering rules.
