install:
	uv sync

run:
	uv run python -m bot.main

lint:
	ruff check .
	mypy .

format:
	black .

test:
	uv run pytest --cov=bot tests/

check: lint test
