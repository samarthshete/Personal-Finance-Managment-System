# BudgetFlow

BudgetFlow is an intelligent personal finance management app with:
- transaction import + categorization
- budgets + alerts
- analytics + reports
- AI advisor
- deterministic investment recommendations

This document covers local development and production deployment hardening.

## Architecture

### Production topology
- Frontend: **Vercel** (Next.js)
- Backend API: **Render or Railway** (FastAPI service)
- Worker: **separate Render/Railway worker service** (`python -m app.worker.worker`)
- Database: **managed PostgreSQL**
- Object storage: **S3-compatible provider**

```text
[Vercel Next.js] -> [FastAPI API service] -> [Managed Postgres]
                                 |-> [S3-compatible storage]
                                 |-> [Worker service consumes async jobs]
```

## Local development

### Prerequisites
- Docker + Docker Compose
- Node.js 18+
- Python 3.11 (for local backend mode)

### Local quickstart (Docker backend)

```bash
cd Implementation/BudgetFlowApp
make up
make migrate-docker
make seed-demo
make health-check

cd frontend
npm install
npm run dev
```

Open:
- frontend: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- health: `http://localhost:8000/health`
- readiness: `http://localhost:8000/health/ready`

### Local quickstart (host backend)

```bash
cd Implementation/BudgetFlowApp
make db-up
make db-wait
make install
cp backend/.env.example backend/.env
make migrate
make run-backend

cd frontend
npm install
npm run dev
```

## Environment configuration

### Backend required variables

- `APP_ENV` (`local|development|production`)
- `DATABASE_URL`
- `SECRET_KEY`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `REFRESH_TOKEN_EXPIRE_DAYS`
- `ADVISOR_ENABLED`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `S3_BUCKET`
- `S3_REGION`
- `S3_FORCE_PATH_STYLE`
- `CORS_ORIGINS`
- `FRONTEND_URL`

See [backend/.env.example](/Users/samarthshete/Documents/OOD Project/Finance Managment System/Implementation/BudgetFlowApp/backend/.env.example).

### Frontend required variables

- `NEXT_PUBLIC_API_BASE_URL`

See [frontend/.env.example](/Users/samarthshete/Documents/OOD Project/Finance Managment System/Implementation/BudgetFlowApp/frontend/.env.example).

### CORS behavior

- local/dev defaults allow localhost frontend origins
- production allows only configured `CORS_ORIGINS` (or `FRONTEND_URL` fallback)

## Health endpoints

- `GET /health` -> app liveness
- `GET /health/ready` -> readiness (DB + storage reachability check)

Both return machine-readable JSON.

## Worker operations

Worker guidance is documented in:
- [docs/WORKER_OPERATIONS.md](/Users/samarthshete/Documents/OOD Project/Finance Managment System/Implementation/BudgetFlowApp/docs/WORKER_OPERATIONS.md)

## Production deployment steps

### 1) Backend API service (Render/Railway)

1. Create a new web service from repo `backend/`.
2. Build command:
   - `pip install -r requirements.txt`
3. Start command:
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Set all backend env vars listed above.
5. Run migrations separately:
   - `alembic upgrade head`

### 2) Worker service (Render/Railway)

1. Create a **separate background worker service** from the same `backend/` code.
2. Use the **same env vars** as backend API.
3. Start command:
   - `python -m app.worker.worker`

### 3) Frontend (Vercel)

1. Import `frontend/` project into Vercel.
2. Set:
   - `NEXT_PUBLIC_API_BASE_URL=https://<your-api-domain>`
3. Deploy.

## CI/CD

GitHub Actions workflows:
- [backend.yml](/Users/samarthshete/Documents/OOD Project/Finance Managment System/Implementation/BudgetFlowApp/.github/workflows/backend.yml): installs backend deps and runs full pytest suite.
- [frontend.yml](/Users/samarthshete/Documents/OOD Project/Finance Managment System/Implementation/BudgetFlowApp/.github/workflows/frontend.yml): installs frontend deps and runs production build.

## Production smoke test checklist

After deployment, verify:
1. `/health` returns `status: ok`.
2. `/health/ready` returns ready with DB connectivity.
3. Login works.
4. Dashboard loads for authenticated user.
5. Async import/report jobs are claimed and completed by worker.
6. Advisor works when `ADVISOR_ENABLED=true` and `OPENAI_API_KEY` is set.
7. Recommendation engine run succeeds and returns outputs.
8. Profile updates persist after reload.
9. Demo seed data is loaded only when intentionally seeded.

## DevOps commands

Key Make targets:
- `make up`
- `make down`
- `make migrate-docker`
- `make test`
- `make seed-demo`
- `make logs-backend`
- `make logs-worker`
- `make health-check`
