#!/bin/sh
set -e

python manage.py migrate --noinput
# After the migration, never before: this row is what the VM checks out, and it
# has to mean "the schema is ready for this commit" rather than "this commit
# exists". Failing to record is not failing to deploy -- the VM simply stays
# where it is, which is the safe direction to fail.
python manage.py record_deployment || true
python manage.py collectstatic --noinput
# ONE HOST RENEWS THE DHAN TOKEN. Renewing invalidates the previous token
# account-wide, so a second renewer silently takes the first one's token away --
# which is precisely what was happening: App Service renewed at 08:20 IST and the
# VM spent every session presenting a token Dhan had already killed, 399 of the
# last 400 order events coming back DH-906. The VM owns renewal because it is the
# host whose IP Dhan whitelists for orders; it publishes the result to the shared
# database and everything here reads it from there.
#
# The gate is its own variable rather than DHAN_TOKEN_FILE. That file is a local
# cache, not a claim of ownership, and conflating the two is what kept this
# failure quiet for a week.
if [ "${DHAN_TOKEN_OWNER:-0}" = "1" ]; then
	python -u manage.py renew_dhan_token --daemon > /home/LogFiles/dhan-token.log 2>&1 &
fi
# Building the shortlist needs a token but not ownership of one -- reading
# history invalidates nothing -- so it runs here, where the page is served. It
# fires at 18:40 IST rather than straight after the close because the history API
# sheds load during the session and a half-downloaded day produces a list that
# looks ordinary and is wrong. Only App Service runs this script, so there is
# exactly one builder however many hosts exist.
python -u manage.py build_daily_shortlist --daemon > /home/LogFiles/daily-shortlist.log 2>&1 &
python -u manage.py collect_index_oi > /home/LogFiles/index-oi-collector.log 2>&1 &
# The dashboard switch controls whether this daemon holds the CAS socket open.
# Packets are persisted to the shared database for replay and model tuning.
python -u manage.py run_cas_nowcast \
  --controlled \
  --output-dir /home/LogFiles/nifty-cas-data \
  > /home/LogFiles/nifty-cas.log 2>&1 &
# The live strategy engine runs on the dedicated VM, not in the App Service.
# App Service manages a pool of outbound IPs that Dhan cannot whitelist. The VM
# has 20.197.60.99 and can place orders; App Service observes the live feed but
# does not trade.
if [ -n "${TELEGRAM_API_ID:-}" ] && [ -n "${TELEGRAM_API_HASH:-}" ] && \
	{ [ -n "${TELEGRAM_SESSION:-}" ] || [ -n "${TELEGRAM_SESSION_STRING:-}" ]; }; then
	python -u manage.py track_telegram > /home/LogFiles/telegram-tracker.log 2>&1 &
fi
exec gunicorn trading_terminal.wsgi:application --bind=0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --worker-class gthread --timeout 120 --max-requests 500 --max-requests-jitter 50
