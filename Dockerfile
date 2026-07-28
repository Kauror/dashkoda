ARG PYTHON_IMAGE=python:3.14.6-slim-bookworm@sha256:4ff4b92a68355dbdb52584ab3391dff8d371a61d4e063468bfd0130e3189c6d9
ARG NODE_IMAGE=node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3

# Frontend build stage. Node exists only here: no Node, npm or node_modules
# reaches the development or production runtime image.
FROM ${NODE_IMAGE} AS frontend-builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

# Tailwind scans the templates, so they must be present when the CSS is built.
COPY frontend ./frontend
COPY apps ./apps
COPY templates ./templates
RUN npm run build

FROM ${PYTHON_IMAGE} AS builder

ARG UV_VERSION=0.11.29
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
COPY --from=frontend-builder /build/static/build ./static/build
RUN POSTGRES_DB=build \
    POSTGRES_USER=build \
    POSTGRES_PASSWORD=build-only-not-a-runtime-secret \
    POSTGRES_HOST=localhost \
    POSTGRES_PORT=5432 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=build-only-not-a-runtime-secret-with-sufficient-length \
    DJANGO_ALLOWED_HOSTS=localhost \
    VIEWER_PIN_HASH=build-only-not-a-runtime-pin-hash \
    VIEWER_PIN_VERSION=1 \
    VIEWER_RATE_LIMIT_SECRET=build-only-not-a-runtime-rate-limit-secret \
    TRUST_CLOUDFLARE_IP_HEADER=false \
    /opt/venv/bin/python manage.py collectstatic --noinput

FROM builder AS development-builder

RUN uv sync --locked --no-install-project

FROM ${PYTHON_IMAGE} AS runtime-base

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 dashkoda \
    && useradd --uid 10001 --gid dashkoda --no-create-home --shell /usr/sbin/nologin dashkoda

WORKDIR /app

FROM runtime-base AS development

ENV UV_CACHE_DIR=/tmp/uv-cache \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=development-builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=development-builder --chown=dashkoda:dashkoda /opt/venv /opt/venv
COPY --from=development-builder --chown=dashkoda:dashkoda /app /app
RUN mkdir -p /app/.pytest_cache \
    && chown dashkoda:dashkoda /app/.pytest_cache

USER dashkoda
EXPOSE 8000

CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--access-logfile=-", "--error-logfile=-", "config.wsgi:application"]

FROM runtime-base AS runtime

COPY --from=builder --chown=dashkoda:dashkoda /opt/venv /opt/venv
COPY --from=builder --chown=dashkoda:dashkoda /app/apps /app/apps
COPY --from=builder --chown=dashkoda:dashkoda /app/config /app/config
COPY --from=builder --chown=dashkoda:dashkoda /app/templates /app/templates
COPY --from=builder --chown=dashkoda:dashkoda /app/static /app/static
COPY --from=builder --chown=dashkoda:dashkoda /app/manage.py /app/manage.py
COPY --from=builder --chown=dashkoda:dashkoda /app/staticfiles /app/staticfiles

USER dashkoda
EXPOSE 8000

CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--access-logfile=-", "--error-logfile=-", "config.wsgi:application"]
