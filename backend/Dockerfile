# Three stages (items 8 and 9, step 14). `base` holds what the application
# runs on; `dev` adds the tests' packages and is what docker-compose.yml
# builds; `runtime`, LAST because Render builds the last stage, is the
# image the deployment runs: no pytest, and not root.

FROM python:3.12-slim AS base

# Unbuffered so log lines appear in `docker compose logs` as they happen,
# which matters once NFR-22's structured stage logging arrives.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source: this layer then survives every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000


# The laptop's image: compose bind-mounts the source over /app, and CI's
# local command runs pytest in it.
FROM base AS dev
# The dev stage is for the laptop only, and stays root because of the bind mount and the root-owned files already in it (item 8, the owner's ruling 1 at step 14).
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]


# What Render runs. The tests stay out with their packages.
FROM base AS runtime
# A user of its own, with no login shell and no home to write to. The
# application writes only its spend ledger and its vector cache, so those
# two directories, and nothing else, are its.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin cartograph
COPY alembic.ini pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY overlays ./overlays
COPY warehouse ./warehouse
COPY eval ./eval
RUN mkdir -p eval/.spend eval/.cache && chown cartograph eval/.spend eval/.cache
USER cartograph
# Migrate, then serve, as in docker-compose.yml (DR-14). On the port the
# host names in $PORT (Render, step 12), 8000 when none is named. No
# --reload: that is the compose file's, for local development.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
