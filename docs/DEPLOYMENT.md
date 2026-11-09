# Deployment guide

## 1. Pick how to run it

**Docker (simplest):**

```bash
cp .env.example .env     # set SECRET_KEY, SESSION_COOKIE_SECURE=1, FLEXAFLOW_BEHIND_PROXY=1
docker compose up -d --build
docker compose exec flexaflow python manage.py createsuperuser
```

Data lives in three volumes: the database (`/data`), `uploads`, and `instance` (secret key, 2FA key, logs).

**Without Docker:**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
gunicorn app:app --bind 127.0.0.1:8000 --workers 3
```

Run it under systemd so it restarts:

```ini
[Service]
WorkingDirectory=/srv/flexaflow
EnvironmentFile=/srv/flexaflow/.env
ExecStart=/srv/flexaflow/.venv/bin/gunicorn app:app --bind 127.0.0.1:8000 --workers 3
Restart=always
User=flexaflow
```

`server.py` (waitress) is fine for development and small internal sites.

## 2. Reverse proxy and HTTPS (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name example.com;
    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    client_max_body_size 128m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
server { listen 80; server_name example.com; return 301 https://$host$request_uri; }
```

Then set `SESSION_COOKIE_SECURE=1` and `FLEXAFLOW_BEHIND_PROXY=1`.

## 3. Database

SQLite works for small sites (one `.db` file; keep it on a local disk). For MySQL set `FLASK_ENV=production` and `DATABASE_URL`, and install a driver (`pip install pymysql`, URL `mysql+pymysql://user:pass@host/db`). Migrations run automatically at startup.

## 4. Caching with several workers

Public pages are cached for anonymous visitors and the cache is cleared on every save. The default `FileSystemCache` (in `instance/cache`) is shared by all workers on one machine. Across several machines use `CACHE_TYPE=RedisCache` with `CACHE_REDIS_URL`. `SimpleCache` is per process, so other workers can show stale pages for up to the cache timeout.

## 5. Backups

Back up together: the database, `uploads/`, and `instance/` (contains `secret_key` and `twofa.key`; keep copies of the 2FA key separate from database dumps). Test a restore. Example nightly job for SQLite:

```bash
sqlite3 /data/cms.db ".backup '/backups/cms-$(date +%F).db'"
tar czf /backups/uploads-$(date +%F).tgz uploads instance
```

## 6. Upgrading

1. Back up.
2. `git pull` (or rebuild the image), `pip install -r requirements.txt`.
3. Restart. Migrations are applied at startup; `python manage.py db-current` shows the revision.

## 7. Monitoring

`GET /healthz` returns 200 and `{"status":"ok"}` when the database answers, 503 otherwise. Logs go to stdout; security events also go to `instance/security.log`.
