FROM python:3.12-slim

# Unbuffered so log lines appear in `docker compose logs` as they happen,
# which matters once NFR-22's structured stage logging arrives.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source: this layer then survives every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Migrate, then serve, as in docker-compose.yml (DR-14). On the port the
# host names in $PORT (Render, step 12), 8000 when none is named. No
# --reload: that is the compose file's, for local development.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
