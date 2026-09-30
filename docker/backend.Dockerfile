FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN mkdir -p /var/lib/ragdesk/uploads && chown -R 10001:10001 /var/lib/ragdesk
ENV PATH="/app/.venv/bin:$PATH" UPLOAD_STORAGE_DIR=/var/lib/ragdesk/uploads
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.container_runtime"]
CMD ["api"]
