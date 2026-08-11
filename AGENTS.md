# MindSurve AI Backend — Agent Instructions

> **Source of truth (product):** `MindSurve Automated Customer Workflow.docx` (frontend repo / shared docs)  
> **Current phase:** Backend **initialization only** — structure, config, DB foundation, Alembic, health check.  
> **Active workflow:** **Beginner only.** Advanced is deferred.  
> **Product name:** MindSurve  

Companion frontend: `mindsurve-ai` (mock-backed UI). This API will eventually replace those mocks.

---

## 1. Mission

Build the MindSurve API that powers the **Beginner** customer journey:

**Beginner (NOW):** Idea → Research → Data → Insights → Website → Ads → CRM → Leads  
**Advanced (LATER — out of scope):** Research Assets → Studies → Respondents → Data → Analysis → AI Insights → Interactive Dashboard

Until explicitly tasked, agents must **not** invent or ship product endpoints, domain models, workers, or auth systems beyond what the current task requires.


Make sure every api yor query you make should be well optimized runs fast like millisecond and return in ms and do no hallucinate and do not run alembic upgrade head you can create migration file but i will only push it in database

---

## 2. Absolute Agent Rules (Non-Negotiable)

### 2.1 No Hallucination

- Do **not** invent APIs, endpoints, fields, tables, queues, or product features that are not in this file, an approved task, or the workflow doc.
- Do **not** implement business logic “for completeness” when the task is scaffolding or a narrow slice.
- Prefer reading existing code over assuming how it works.
- If something is unspecified, ask or implement the smallest reasonable foundation — do not invent a full subsystem.

### 2.2 Scope Discipline

- Implement **only** what the current task asks for.
- Do not start studies, jobs/workers, CRM, ads, or website generation unless the task explicitly says so.
- Do not add libraries without a clear need stated in the task.
- Keep diffs focused — no drive-by refactors.

### 2.3 Secrets & Safety

- Never commit real credentials, API keys, tokens, or production connection strings.
- `.env` is gitignored; only `.env.example` with placeholders may be committed.
- **Never log** passwords, API keys, database credentials, authorization tokens, or sensitive user information.
- Do not hardcode production URLs or fake security.

### 2.4 Database & Migrations

- Use SQLAlchemy 2.x style (mapped classes, `select()`, typed sessions).
- Schema changes go through **Alembic** — no ad-hoc `create_all` in production paths.
- Alembic must read `DATABASE_URL` from application settings (`app.core.config`), not duplicate secrets in `alembic.ini`.
- Application **startup must not fail** solely because `DATABASE_URL` is unset (health / import must work).
- Do not create fake migrations or placeholder tables.
- **Create migration files when models change, but do not run `alembic upgrade head`** — the human applies migrations to the database.

### 2.4.1 Indexing (required)

- **Index every column used for filtering, joining, ownership checks, or sorting** whenever practical.
- Required patterns for new tables:
  - Foreign keys must be indexed (SQLAlchemy `index=True` and/or composite indexes).
  - Ownership lookups: `(user_id, …)`, `(project_id, …)`, etc.
  - List endpoints: composite indexes matching `WHERE` + `ORDER BY` (e.g. `(user_id, updated_at)`, `(project_id, updated_at)`, `(chat_id, created_at)`).
  - Unique business keys (e.g. email) enforced in the database, not only in app code.
- Prefer composite indexes that match real query shapes over many single-column indexes.
- Do not add redundant indexes blindly — justify each against an actual query path.

### 2.4.2 No N+1 queries (required)

- **Never** load a list and then query related rows one-by-one in a loop.
- Use joins, `IN` batches, window/aggregate subqueries, or `selectinload`/`joinedload` as appropriate.
- List endpoints that need previews/counts must fetch them in **O(1) round-trips** (typically 1–2 queries total), not O(n).
- Review new repository methods for accidental N+1 before merging.

### 2.5 API Design

- Versioned HTTP API under `/api/v1`.
- Keep `GET /health` for liveness (no DB required).
- Use Pydantic v2 schemas in `app/schemas/` for request/response models.
- Repositories for data access; services for orchestration — when those layers are introduced for a feature.
- Return clear, consistent error shapes; never leak stack traces to clients in production.
- Optimize for low-latency responses (indexed filters, lean payloads, no unnecessary round-trips).

### 2.6 Code Quality

- Python 3.11+; type hints on public functions and API boundaries.
- No `Any` unless truly unavoidable and commented.
- Prefer explicit names over clever abstractions.
- Tests with pytest; use `httpx` / FastAPI `TestClient` for API tests.
- Match existing package layout before inventing new top-level packages.

---

## 3. Product Context (For Future Implementation)

### 3.1 Beginner pipeline (reference)

```
CREATE PROJECT → UPLOAD RESOURCES → AI UNDERSTANDS PROJECT
→ CREATE LOGO STUDY + TEXT STUDY → ADMIN REVIEW → STUDIES LIVE
→ RESPONDENT COLLECTION → VALIDATION → ANALYSIS → RESULTS
→ AI RESULTS ASSISTANT → WEBSITE → META ADS → CRM → LEAD EMAILS
→ FINAL DELIVERY
```

### 3.2 Unified project state machine (Beginner)

```
CREATED → PROCESSING_INPUT → PREPARING_STUDIES → AWAITING_REVIEW
→ STUDIES_BEING_PREPARED → STUDIES_LIVE → COLLECTING_RESPONSES
→ VALIDATING_RESPONSES → ANALYZING_DATA → GENERATING_RESULTS
→ RESULTS_READY → GENERATING_WEBSITE → DEPLOYING_WEBSITE
→ SETTING_UP_ADS → SETTING_UP_CRM → CONFIGURING_EMAILS → COMPLETED
```

(Also allow generic deliverable states where they map cleanly; do not invent Advanced dashboard states until unblocked.)

### 3.3 Domain concepts (align with frontend `types/`)

| Entity | Notes |
|--------|--------|
| **User** | Auth identity (later) |
| **Project** | Parent container; Beginner workflow |
| **Chat / Message** | Conversation under a project (frontend already mocks this) |
| **Study** | Logo/Visual and/or Text MindGenomic study |
| **Respondents** | Collection progress / validation |
| **Results / Deliverables** | Insights, website, ads, CRM, leads |
| **Activity Event / Job** | Timeline + async work |

Do **not** create these models until the relevant implementation task starts.

### 3.4 Explicitly out of scope until requested

- Advanced customer workflow and interactive dashboard
- Real Cint / Meta / Netlify / CRM integrations (stubs only when tasked)
- Admin/worker UIs
- Full distributed observability stack
- Invented “enterprise” middleware layers with no callers

---

## 4. Target Repository Structure

```
mindsurve-ai-backend/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── alembic.ini
├── .env.example
├── app/
│   ├── main.py                 # FastAPI app + /health
│   ├── api/v1/router.py        # Versioned router mount point
│   ├── core/                   # config, logging, security stubs
│   ├── db/                     # base, session, models/
│   ├── schemas/
│   ├── services/
│   ├── repositories/
│   ├── dependencies/
│   ├── middleware/
│   └── utils/
├── alembic/
│   ├── env.py
│   └── versions/
└── tests/
    ├── conftest.py
    ├── unit/
    └── integration/
```

**Rules:**

- `app/api/v1/` — HTTP route modules only; thin handlers.
- `app/services/` — business orchestration.
- `app/repositories/` — persistence.
- `app/core/` — cross-cutting config/logging/security.
- `app/db/models/` — SQLAlchemy models; import them in `models/__init__.py` for Alembic discovery.
- Do not dump everything into `main.py`.

---

## 5. Configuration

- Use **pydantic-settings** in `app/core/config.py`.
- Required eventual settings: `APP_NAME`, `APP_ENV`, `DEBUG`, `DATABASE_URL`.
- Load from environment / `.env`.
- Document new env vars in `.env.example` with placeholders only.

---

## 6. Logging

- Use `app/core/logging.py` as the single setup entry point.
- Structured, consistent log format is enough for now.
- Never log secrets or sensitive PII.

---

## 7. Security Module

- `app/core/security.py` is reserved for future auth utilities.
- Do **not** invent JWT/OAuth/password flows until an auth task is assigned.

---

## 8. Testing Expectations

- Every new API behavior should get at least one focused test.
- Prefer fast unit tests; integration tests when DB/API wiring matters.
- Do not build a giant test framework up front.
- Health check must remain green without a live database.

---

## 9. How Agents Should Work Tasks

1. Read this file and the specific user task before coding.
2. Implement only the requested slice (API + models + migration + tests when applicable).
3. Keep `DATABASE_URL` optional for app import/startup unless the task is specifically about DB connectivity.
4. Update `.env.example` / README only when new real setup steps appear.
5. Stop when the task is done — do not “continue” into the next product area unprompted.

---

## 10. Definition of Done (Any Backend Task)

- [ ] Matches the assigned task (no invented product behavior)
- [ ] Types / schemas / models consistent with agreed domain language
- [ ] No secrets in git; no sensitive data in logs
- [ ] Alembic used for schema changes when models change
- [ ] Tests added or updated for the change
- [ ] App still imports; `/health` still works without requiring DB (unless task says otherwise)
- [ ] Diff is focused

---

## 11. Implementation Status

### Done

- Project scaffold (venv, FastAPI, SQLAlchemy, Alembic, pytest)
- **Authentication**
  - `User` + `AuthSession` models and Alembic migration
  - Endpoints: register / login / refresh / logout / me
  - bcrypt password hashing; JWT access tokens; hashed refresh tokens in DB
  - HttpOnly refresh cookie (30-day sessions); access token ~15 minutes
  - CORS via `FRONTEND_URL` (credentials-aware; never `*`)
  - Integration tests for the auth happy-path and failure cases
- **Projects + Chats**
  - Models: `Project`, `Chat`, `ChatMessage` + migration `20260324_0002`
  - Indexed ownership/list paths; chat previews without N+1
  - Endpoints under `/api/v1/projects` and `/api/v1/chats`
  - Cascade delete project → chats → messages
  - Integration tests for CRUD, ownership isolation, start-chat flow

### Not started

- Studies, respondents, results, deliverables
- Workers / queues / integrations
- Password-reset email flow
- OAuth / social login

---

*Keep this file aligned with the product workflow doc and the frontend `AGENTS.md` when the product scope changes.*
