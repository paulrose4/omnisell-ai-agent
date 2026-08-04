FROM public.ecr.aws/docker/library/python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 mock
WORKDIR /app
COPY local_e2e_mock/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=mock:mock local_e2e_mock ./local_e2e_mock
COPY --chown=mock:mock application ./application
USER mock
EXPOSE 9000
CMD ["uvicorn", "local_e2e_mock.server:app", "--host", "0.0.0.0", "--port", "9000"]
