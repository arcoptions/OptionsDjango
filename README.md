# ARC Options Terminal V1 (Django)

This module is the V1 implementation based on frozen requirements:
- Options only
- Telegram + Manual + Chartink inputs
- OI updates at 60/120s (default 60)
- Single 0-100 score
- Intraday + Swing
- Direction-only suggestions
- Dhan Super Order policy A (Entry + SL + T1)
- Hard risk blocks: max 5/day and max 5 open trades
- Mandatory trade journal
- Monthly archive by expiry and close month
- Legacy sync actions for Telegram, Chartink triggers, and Index OI from arc_trading.db

## Quick Start

1. Install dependencies from repo root:
   - pip install -r requirements.txt
2. Run migrations:
   - cd django_v1
   - python manage.py makemigrations
   - python manage.py migrate
3. Create admin user (optional):
   - python manage.py createsuperuser
4. Run server:
   - python manage.py runserver

## Azure App Service

Use a Linux Web App with Python 3.13. The Basic B1 plan is suitable for a small always-on deployment; Free F1 is useful only for evaluation and has limited compute and no always-on support.

1. In the Azure portal, create an **App Service > Web App** in the subscription containing your credit.
2. Choose **Code**, **Python 3.13**, **Linux**, and a nearby region. Select the lowest plan that meets your uptime needs.
3. Under **Deployment Center**, connect this GitHub repository and the `master` branch.
4. Under **Configuration > General settings**, set the startup command to `sh startup.sh` and enable HTTPS Only.
5. Under **Environment variables**, add:
   - `DJANGO_DEBUG=0`
   - `DJANGO_SECRET_KEY=<a long random value>`
   - `DJANGO_ALLOWED_HOSTS=<app-name>.azurewebsites.net`
   - `DJANGO_CSRF_TRUSTED_ORIGINS=https://<app-name>.azurewebsites.net`
   - `SCM_DO_BUILD_DURING_DEPLOYMENT=true`
6. For persistent production data, create **Azure Database for PostgreSQL Flexible Server**, create a database, and set `DATABASE_URL=postgresql://<user>:<url-encoded-password>@<server>.postgres.database.azure.com:5432/<database>?sslmode=require`. Configure its networking to allow the Web App to connect.
7. Restart the Web App. The startup script applies migrations, collects static files, and starts Gunicorn.

Without `DATABASE_URL`, the app uses SQLite. That is acceptable for a short single-instance evaluation, but deployments can replace the database file and multiple App Service instances cannot safely share it.

For Dhan order placement, also set `DHAN_ACCESS_TOKEN` and `DHAN_CLIENT_ID`. A valid `security_id` is required per signal; `DHAN_SECURITY_ID_FALLBACK` should only be used for testing. Set `TELEGRAM_INGEST_TOKEN` if the Telegram ingestion endpoint is exposed.
