# syntax=docker/dockerfile:1.7
# ---- builder ---------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt \
 && /opt/venv/bin/pip install gunicorn

# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# Run unprivileged: a container escape should not land on root.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --create-home app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY --chown=app:app . .

# Collect static at build time so the image is immutable at runtime. The dummy
# key is only used by this command; the real one comes from the environment.
RUN SECRET_KEY=build-only DEBUG=False /opt/venv/bin/python manage.py collectstatic --noinput \
 && chown -R app:app /srv/staticfiles

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/admin/login/',timeout=3); sys.exit(0 if r.status==200 else 1)"

CMD ["gunicorn", "config.wsgi:application", \
     "--workers", "3", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
