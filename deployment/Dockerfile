# Repo Necromancer: one image with the API and the built dashboard.
# Build from the repository root:  docker build -f deployment/Dockerfile -t necromancer .

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NECRO_DATA_DIR=/data \
    NECRO_SANDBOX=off
# git is needed to clone repositories. pytest runs the bundled fixtures' baseline tests.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 necro \
    && mkdir /data && chown necro /data
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt pytest==9.2.0
COPY backend/app backend/app
COPY fixtures fixtures
COPY --from=web /web/dist frontend/dist
USER necro
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
