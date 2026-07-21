# Manual setup (without scripts/)

Prerequisites: Python 3.12+, Node.js 20+, Docker & Docker Compose.

## Database

```bash
docker compose -f docker/docker-compose.yml up -d db
```

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # adjust DATABASE_URL etc. if needed
uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs`.

Backend quality checks:

```bash
ruff check .
black --check .
mypy app
pytest
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

App at `http://localhost:5173`.

Frontend quality checks:

```bash
npm run lint
npm run format:check
npx tsc -b
npm run test
npm run build
```

## Full stack via Docker Compose

```bash
docker compose -f docker/docker-compose.yml up --build
```

- Frontend (Nginx, serving the production build, proxying `/api` to the backend): `http://localhost:8080`
- Backend: `http://localhost:8000`
- Postgres: `localhost:5432`
