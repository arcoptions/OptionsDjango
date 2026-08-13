#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python -u manage.py collect_index_oi > /home/LogFiles/index-oi-collector.log 2>&1 &
if [ -n "${TELEGRAM_API_ID:-}" ] && [ -n "${TELEGRAM_API_HASH:-}" ] && \
	{ [ -n "${TELEGRAM_SESSION:-}" ] || [ -n "${TELEGRAM_SESSION_STRING:-}" ]; }; then
	python -u manage.py track_telegram > /home/LogFiles/telegram-tracker.log 2>&1 &
fi
exec gunicorn trading_terminal.wsgi:application --bind=0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --worker-class gthread --timeout 120 --max-requests 500 --max-requests-jitter 50