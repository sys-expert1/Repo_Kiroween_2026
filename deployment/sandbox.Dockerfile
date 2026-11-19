# Image used by the restricted sandbox to run a repository's tests.
# The sandbox starts it with --user 65534, --read-only, --network none, --cap-drop ALL and
# CPU/memory/PID limits (see backend/app/execution/sandbox.py). It contains only Python and pytest.
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir pytest==9.2.0

USER 65534:65534
WORKDIR /work
