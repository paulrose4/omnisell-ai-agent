FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S sandbox_group \
    && adduser -S sandbox_user -G sandbox_group \
    && pip install --no-cache-dir requests

USER sandbox_user
WORKDIR /home/sandbox_user

CMD ["python", "-c", "print('OmniSell sandbox ready')"]
