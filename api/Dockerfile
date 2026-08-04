FROM python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 omnisell
WORKDIR /app
COPY requirements.runtime.txt .
RUN pip install --requirement requirements.runtime.txt
# Perl is not an application runtime dependency and currently carries an unfixed Critical CVE.
RUN apt-get purge --yes --allow-remove-essential perl-base \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=omnisell:omnisell . .
RUN mkdir -p /app/tmp && chown -R omnisell:omnisell /app
USER omnisell
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
