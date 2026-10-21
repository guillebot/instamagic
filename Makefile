.PHONY: up down test lint

up:
	docker compose up -d --build

down:
	docker compose down

test:
	cd backend && .venv/bin/pytest -q

lint:
	cd backend && .venv/bin/ruff check instamagic tests
	cd frontend && npx tsc --noEmit
