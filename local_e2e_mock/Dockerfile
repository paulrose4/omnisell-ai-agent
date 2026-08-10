ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
FROM ${PYTHON_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 mock
WORKDIR /app
COPY local_e2e_mock/requirements.lock.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_NO_CACHE_DIR=0 pip install \
    --index-url "${PIP_INDEX_URL}" \
    --require-hashes \
    --requirement requirements.lock.txt
COPY --chown=mock:mock local_e2e_mock ./local_e2e_mock
COPY --chown=mock:mock application ./application
USER mock
EXPOSE 9000
CMD ["uvicorn", "local_e2e_mock.server:app", "--host", "0.0.0.0", "--port", "9000"]
