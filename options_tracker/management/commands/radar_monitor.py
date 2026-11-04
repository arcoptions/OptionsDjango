"""Independent local readiness monitor; host-loss detection needs an external host."""

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker import telegram_bot
from options_tracker.models import AppSetting, RadarAssistantSnapshot, RadarInstrument, RadarWorker
from options_tracker.radar_assistant import worker_health
from options_tracker.radar_feed import acquire_worker, heartbeat
from options_tracker.radar_history import credentials
from options_tracker.services import _token_expiry, get_dhan_credentials


def upcoming_credential_expiry(now):
    token, _ = get_dhan_credentials()
    expiry = _token_expiry(token) if token else None
    return expiry if expiry and now < expiry <= now+timezone.timedelta(hours=24) else None


def problems(now):
    health = worker_health()
    failures = []
    for name in ["paper-radar", "radar-evaluator", "paper-alerts"]:
        if not health["workers"][name]["alive"]:
            failures.append(f"{name}: heartbeat/lease unavailable")
    news = RadarWorker.objects.filter(name="radar-news").first()
    if not news or news.lease_until <= now or now-news.heartbeat_at > timezone.timedelta(seconds=90):
        failures.append("Public BeatTheStreet collector offline/stale")
    if health["market_open_weekday_only"]:
        if not health["healthy"]:
            failures.append(f"Fresh equity coverage below 80% ({health['fresh']}/{health['universe']})")
        if not RadarAssistantSnapshot.objects.filter(expires_at__gt=now, timestamp__lte=now).exists():
            failures.append("No fresh priority snapshot; no actionable shortlist")
    try:
        credentials()
    except ValueError:
        failures.append("Dhan credentials absent/expired; renew through local settings")
    if RadarInstrument.objects.filter(active=True, refreshed_at__lt=now-timezone.timedelta(hours=24)).exists():
        failures.append("Instrument master is older than 24h; run daily preflight")
    return failures


def monitor_once(now):
    failures = problems(now)
    row = AppSetting.objects.filter(key="radar_monitor_state").first()
    previous = json.loads(row.value) if row else {}
    signature = "|".join(failures)
    changed = signature != previous.get("signature", "")
    last = previous.get("attempted_at")
    due = last is None or (now-timezone.datetime.fromisoformat(last)).total_seconds() >= 900
    notify = (bool(failures) and (changed or due)) or (not failures and bool(previous.get("signature")))
    state = {"signature": signature, "checked_at": now.isoformat(),
             "attempted_at": previous.get("attempted_at"), "delivery_error": previous.get("delivery_error", ""),
             "warned_expiry": previous.get("warned_expiry")}
    if notify:
        text = ("<b>ARC assistant needs attention</b>\n"+"\n".join(failures) if failures else
                "<b>ARC assistant recovered</b>\nLocal worker/data readiness checks are passing.")
        ok, error = telegram_bot.send(text)
        state["attempted_at"] = now.isoformat()
        state["delivery_error"] = "" if ok else error or "Monitor notification failed."
        if not ok:
            AppSetting.objects.update_or_create(key="radar_monitor_state", defaults={"value": json.dumps(state)})
            raise ValueError(f"Health notification failed: {state['delivery_error']}")
    expiry = upcoming_credential_expiry(now)
    warned = bool(expiry and state["warned_expiry"] != expiry.isoformat())
    if warned and expiry is not None:
        ok, error = telegram_bot.send(
            "<b>ARC Dhan credential renewal reminder</b>\n"
            f"The configured token expires {timezone.localtime(expiry):%d %b %Y %H:%M:%S %Z}.\n"
            "Renew it through local Dhan settings before the next trading session. "
            "Expired credentials stop collection and paper action eligibility. No token is included in this message."
        )
        if not ok:
            state["delivery_error"] = error or "Credential reminder delivery failed."
            AppSetting.objects.update_or_create(key="radar_monitor_state", defaults={"value": json.dumps(state)})
            raise ValueError(f"Credential reminder failed: {state['delivery_error']}")
        state["warned_expiry"] = expiry.isoformat()
        state["delivery_error"] = ""
    AppSetting.objects.update_or_create(key="radar_monitor_state", defaults={"value": json.dumps(state)})
    return {"healthy": not failures, "problems": failures, "notified": notify or warned,
            "credential_expires_at": expiry.isoformat() if expiry else None}


class Command(BaseCommand):
    help = "Separate heartbeat/credential monitor with throttled Telegram failure/recovery notices."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        owner = None
        try:
            owner = acquire_worker(timezone.now(), name="radar-monitor")
            while True:
                now = timezone.now()
                heartbeat(owner, "CHECKING", "Checking independent workers.", now, name="radar-monitor")
                result = monitor_once(now)
                heartbeat(owner, "HEALTHY" if result["healthy"] else "DEGRADED", json.dumps(result), timezone.now(), name="radar-monitor")
                if options["once"]:
                    self.stdout.write(json.dumps(result))
                    return
                time.sleep(20)
        except KeyboardInterrupt:
            self.stdout.write("Local radar monitor stopped.")
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        finally:
            if owner:
                RadarWorker.objects.filter(name="radar-monitor", owner=owner).update(state="STOPPED", lease_until=timezone.now())
