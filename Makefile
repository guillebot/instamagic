.PHONY: up down test test-security lint ci deploy-stage

up:
	docker compose up -d --build

down:
	docker compose down

deploy-stage:
	scripts/deploy-stage.sh

test:
	@if [ -x backend/.venv/bin/python ]; then \
		cd backend && .venv/bin/python -m pytest -q; \
	else \
		cd backend && python3 -m pytest -q; \
	fi

test-security:
	@if [ -x backend/.venv/bin/python ]; then \
		cd backend && .venv/bin/python -m pytest -q \
			tests/test_security_*.py \
			tests/test_gates.py \
			tests/test_decision.py \
			tests/test_paths.py \
			tests/test_gemini_context.py; \
	else \
		cd backend && python3 -m pytest -q \
			tests/test_security_*.py \
			tests/test_gates.py \
			tests/test_decision.py \
			tests/test_paths.py \
			tests/test_gemini_context.py; \
	fi

lint:
	@if [ -x backend/.venv/bin/python ]; then \
		cd backend && .venv/bin/ruff check instamagic tests; \
	else \
		cd backend && python3 -m ruff check instamagic tests; \
	fi
	cd frontend && npm exec -- tsc --noEmit

ci: test lint
