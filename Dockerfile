FROM python:3.12-slim

RUN pip install uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# SQLite и .env: монтировать volume на /app/bot/database и положить .env в /app или /app/bot
ENV PYTHONPATH=/app
CMD ["python", "-m", "bot.main"]
