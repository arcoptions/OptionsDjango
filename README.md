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

## Railway

- `Procfile` is included for Gunicorn startup.
- Set environment variables from `.env.example`.
- For Dhan order placement, set `DHAN_ACCESS_TOKEN` and `DHAN_CLIENT_ID`.
- Dhan order placement also requires a valid `security_id` per signal; fallback `DHAN_SECURITY_ID_FALLBACK` should only be used for testing.
