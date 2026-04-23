# BudgetFlow Production Deploy Runbook

This runbook is intentionally procedural and limited to deployment operations.
It does not include product changes.

## 1) Pre-deploy Gate (must be green)

From repo root:

```bash
cd backend
.venv/bin/python -m pytest -q

cd ../frontend
npm run build

cd ..
make up
make health-check
```

Expected:
- Backend tests: pass
- Frontend production build: pass
- `/health` and `/health/ready`: valid JSON responses

## 2) Required Production Variables

Backend API + Worker:

- `APP_ENV=production`
- `DATABASE_URL`
- `SECRET_KEY`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `REFRESH_TOKEN_EXPIRE_DAYS`
- `FRONTEND_URL`
- `CORS_ORIGINS`
- `ADVISOR_ENABLED`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `S3_BUCKET`
- `S3_REGION`
- `S3_FORCE_PATH_STYLE`

Frontend:

- `NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>`

## 3) Provisioning Targets

- Frontend: Vercel
- Backend API: Render or Railway web service
- Worker: separate Render or Railway service
- Database: managed Postgres
- Storage: S3-compatible object storage

## 4) Backend API Deploy (Render/Railway)

Service type: web service

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Build command:

```bash
pip install -r requirements.txt
```

After env vars are set, run migrations against production DB:

```bash
cd backend
alembic upgrade head
```

Validate:

```bash
curl -fsS https://<backend-domain>/health
curl -fsS https://<backend-domain>/health/ready
```

## 5) Worker Deploy (Render/Railway)

Service type: background worker

Start command:

```bash
python -m app.worker.worker
```

Use the same env vars as backend (including `DATABASE_URL`, S3 vars, advisor vars).

Validate worker logs:
- startup event appears
- no crash loop
- pending jobs are claimed and completed

## 6) Frontend Deploy (Vercel)

Project root: `frontend/`

Set:

- `NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>`

Deploy and verify:
- login/signup
- authenticated pages load
- API calls succeed to deployed backend

## 7) Smoke Test (Production URLs)

Required checks:
1. Signup/login works.
2. Dashboard loads.
3. Profile save/load works.
4. CSV import creates async job.
5. Worker processes import to completion.
6. Report generation completes and download returns 200.
7. Advisor works when enabled.
8. Recommendations endpoint works.
9. `/health` and `/health/ready` pass.
10. CORS allows deployed frontend origin and blocks unrelated origins.

## 8) Demo Seed (only after stable deployment)

Run once against production DB:

```bash
cd backend
python -m app.scripts.seed_demo_data
```

Verify login:
- `healthy@example.com`
- `stressed@example.com`
- `newuser@example.com`

Password:
- `DemoPass123!`

## 9) Rollback and Safety Notes

- Do not run schema downgrades unless explicitly required.
- Keep worker as a separate service.
- Do not seed demo users before smoke tests pass.
- If readiness fails, inspect DB and storage credentials first.
