# The plystore service. Built from the plystore folder (the build context): docker build -f docker/Dockerfile .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /usr/local/bin/

RUN groupadd -g 1000 ply && useradd -m -u 1000 -g 1000 ply

WORKDIR /opt/plystore
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# the client and the server extra, from the lock
RUN UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --extra server

ENV PATH="/opt/plystore/.venv/bin:$PATH"

USER ply
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=4)"]
CMD ["plystore-serve"]
