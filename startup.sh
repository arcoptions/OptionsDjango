#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput
# Dhan tokens die after 24 hours and only a live one can be renewed, so this has
# to run well before it lapses rather than after. Twice a day leaves a full spare
# cycle if one attempt fails. Needs DHAN_TOKEN_FILE, or there is nowhere to keep
# the result and the renewal would throw away a working token.
if [ -n "${DHAN_TOKEN_FILE:-}" ]; then
	(
		while true; do
			python -u manage.py renew_dhan_token || echo "token renewal failed; the old token stands until it lapses"
			sleep 43200
		done
	) > /home/LogFiles/dhan-token.log 2>&1 &
fi
python -u manage.py collect_index_oi > /home/LogFiles/index-oi-collector.log 2>&1 &
# The live strategy engine. It also refuses to act unless the nifty_live_enabled
# AppSetting is on, so this line starting it is not the same as it trading.
if [ -n "${NIFTY_LIVE_ENABLED:-}" ]; then
	python -u manage.py run_nifty_live > /home/LogFiles/nifty-live.log 2>&1 &
fi
if [ -n "${TELEGRAM_API_ID:-}" ] && [ -n "${TELEGRAM_API_HASH:-}" ] && \
	{ [ -n "${TELEGRAM_SESSION:-}" ] || [ -n "${TELEGRAM_SESSION_STRING:-}" ]; }; then
	python -u manage.py track_telegram > /home/LogFiles/telegram-tracker.log 2>&1 &
fi
exec gunicorn trading_terminal.wsgi:application --bind=0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --worker-class gthread --timeout 120 --max-requests 500 --max-requests-jitter 50