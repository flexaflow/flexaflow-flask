FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/cms.db \
    FLEXAFLOW_BEHIND_PROXY=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home app && mkdir -p /data /app/instance /app/uploads \
    && chown -R app:app /data /app
USER app
VOLUME ["/data", "/app/instance", "/app/uploads"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
