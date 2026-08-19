#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput
# Dhan tokens die after 24 hours and only a live one can be renewed, so this has
# to run well before it lapses rather than after. The renewal daemon schedules
# against the clock (08:20 IST) rather than from process start, so renewal never
# lands during a session. It needs DHAN_TOKEN_FILE, but that is only set on the
# order-placing host (the VM); App Service reads the token it renewed from the
# database instead.
if [ -n "${DHAN_TOKEN_FILE:-}" ]; then
	python -u manage.py renew_dhan_token --daemon > /home/LogFiles/dhan-token.log 2>&1 &
	# Same gate, same reason: building the shortlist means refreshing equity bars
	# from Dhan, so it can only run where the token lives. It fires at 18:40 IST
	# rather than straight after the close because the history API sheds load
	# during the session and a half-downloaded day produces a list that looks
	# ordinary and is wrong. App Service serves /shortlist/ from the rows the VM
	# writes; it does not build them.
	python -u manage.py build_daily_shortlist --daemon > /home/LogFiles/daily-shortlist.log 2>&1 &
fi
python -u manage.py collect_index_oi > /home/LogFiles/index-oi-collector.log 2>&1 &
# The live strategy engine runs on the dedicated VM, not in the App Service.
# Only one order-placing host can hold the Dhan token at any moment, and
# App Service manages a pool of outbound IPs that Dhan cannot whitelist. The VM
# has 20.197.60.99 and can place orders; App Service observes the live feed but
# does not trade.
if [ -n "${TELEGRAM_API_ID:-}" ] && [ -n "${TELEGRAM_API_HASH:-}" ] && \
	{ [ -n "${TELEGRAM_SESSION:-}" ] || [ -n "${TELEGRAM_SESSION_STRING:-}" ]; }; then
	python -u manage.py track_telegram > /home/LogFiles/telegram-tracker.log 2>&1 &
fi
exec gunicorn trading_terminal.wsgi:application --bind=0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --worker-class gthread --timeout 120 --max-requests 500 --max-requests-jitter 50