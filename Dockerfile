# Minimal image for running the carbon-badge CLI.
#   docker build -t carbon-badge .
#   docker run --rm -e GITHUB_TOKEN carbon-badge owner/repo > badge.json

# --- build stage: produce a wheel ---
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS build
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel

# --- runtime stage ---
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4
RUN adduser --disabled-password --uid 10001 app
WORKDIR /app
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER app

# Default use is one-shot (see usage above); the optional --serve mode is
# self-checking (http.server.serve_forever binds immediately or fails fast),
# so this just confirms the interpreter starts.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=1 \
    CMD ["python3", "-c", "import sys; sys.exit(0)"]

ENTRYPOINT ["carbon-badge"]
