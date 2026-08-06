ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
FROM ${PYTHON_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 mock
WORKDIR /app

COPY requirements.lock.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_NO_CACHE_DIR=0 pip install \
    --index-url "${PIP_INDEX_URL}" \
    --require-hashes \
    --requirement requirements.lock.txt

COPY --chown=mock:mock . .

USER mock
EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
