# The trading VM

One Azure VM (`vm-options-trader`, 20.197.60.99) runs the live engine. It exists
because Dhan whitelists a single IP for order placement and App Service manages
a pool of outbound addresses that cannot be whitelisted. Everything else — the
site, the OI collector, the shortlist builder — runs in App Service against the
same Azure Postgres.

```
ssh -i ~/.ssh/optionsvm azureuser@20.197.60.99
```

- Checkout: `/opt/optionsdjango` (lowercase)
- Virtualenv: `/opt/optionsdjango/.venv`
- Environment: `/etc/optionsdjango.env`, root-only. azureuser has passwordless
  sudo, so read it with
  `sudo -n bash -c 'set -a; . /etc/optionsdjango.env; ...'`. Sourcing it as
  azureuser fails silently and Django then falls back to the local
  `db.sqlite3` — which on this box is a **0-byte decoy**. The real database is
  shared Postgres, so `showmigrations` without the env loaded reports every
  migration unapplied and is telling you nothing.

## What runs here

| Unit | Does |
|---|---|
| `nifty-live.service` | The trading engine, `manage.py run_nifty_live` |
| `dhan-token.service` | Renews the Dhan token at 08:20 IST and publishes it to Postgres |
| `optionsdjango-sync.timer` | Every 5 min: check out the commit App Service deployed, restart the engine when it is safe |

The VM does **not** migrate. App Service owns the schema, and the deploy marker
it writes (`AppSetting['deployed_commit']`) is only written after `migrate`
succeeds — so following that marker means the VM never runs code whose schema is
not in place yet.

## Installing

```sh
sudo cp deploy/vm/*.service deploy/vm/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nifty-live.service dhan-token.service optionsdjango-sync.timer
```

Set `DHAN_TOKEN_FILE=/opt/optionsdjango/.dhan_token` in
`/etc/optionsdjango.env`. On this host the file is a local cache that keeps the
engine alive through a database blip; the database row is the copy the other
host reads. App Service sets `DHAN_TOKEN_OWNER=0` (or nothing) and never renews.

## Checking on it

```sh
systemctl status nifty-live dhan-token
systemctl list-timers optionsdjango-sync
journalctl -u optionsdjango-sync -n 50
/opt/optionsdjango/.venv/bin/python manage.py renew_dhan_token --check
/opt/optionsdjango/.venv/bin/python manage.py sync_to_deployed_commit --dry-run
```

The `/nifty-live/` page shows the commit this box is on beside the one App
Service deployed, and says `CODE DRIFT` when they differ. That row is written by
the sync timer on every run, so a timestamp that stops moving means the timer
died — which otherwise looks exactly like being in sync.

## Seeding /shortlist/ on a fresh database

The shortlist reads `TrackedStock` and 15-minute `StockEquityCandle`. Only the
first ships with the app; the candles are a download and are pulled here, where
the Dhan token lives. Run it outside market hours — the history API sheds load
between 10:00 and 14:00 IST, measured 42–49% success against 100% after 18:00.

```sh
sudo -n bash -c 'set -a; . /etc/optionsdjango.env; cd /opt/optionsdjango
  .venv/bin/python manage.py seed_reference_data
  .venv/bin/python manage.py download_stock_data --feed master
  .venv/bin/python manage.py download_stock_data --feed equity --months 10
  .venv/bin/python manage.py build_daily_shortlist --rebuild-history 400'
```

The equity download is resumable — finished windows are recorded and skipped —
so a run that dies partway can simply be repeated.

`refresh_token.sh` used to run before the engine and is gone. It copied the
token from `/mnt/arc-options`, a storage mount that was never configured on
either host: it printed "Azure mount not found; token must be pre-placed" and
exited 0 on every boot, so the step meant to keep this host's token current had
never once run.
