"""Own only our child PIDs; restart failed paper workers with bounded backoff."""

from pathlib import Path
import os
import subprocess
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from options_tracker.models import RadarWorker
from options_tracker.radar_feed import acquire_worker, heartbeat


def worker_specs():
    specs = {
        "collector": ["radar_run", "--stream", "--collect-only"],
        "evaluator": ["radar_evaluate", "--loop"],
        "delivery": ["radar_deliver", "--loop"],
        "reconcile": ["radar_reconcile", "--loop", "--accept-incomplete"],
        "monitor": ["radar_monitor", "--loop"],
        "news": ["radar_news", "--loop"],
    }
    if all(os.getenv(key, "").strip() for key in ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"]) and (
        os.getenv("TELEGRAM_SESSION") or os.getenv("TELEGRAM_SESSION_STRING")
    ):
        specs["session-news"] = ["track_telegram"]
    if (
        os.getenv("ARC_DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "production"
        and os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    ):
        specs["token-reminder"] = ["remind_dhan_token", "--apply", "--loop"]
    return specs


def daily_preflight_window(local):
    minute = local.hour*60+local.minute
    return local.weekday() < 5 and 8*60+40 <= minute < 9*60+15


def child_heartbeat_timed_out(worker, launched_at, elapsed, now):
    current_generation = bool(worker and worker.heartbeat_at >= launched_at)
    if not current_generation:
        return elapsed > 600
    return now-worker.heartbeat_at > timezone.timedelta(seconds=120)


class Command(BaseCommand):
    help = "Supervise local paper workers. Install with your OS scheduler for logon/restart; not a cloud deployment."

    def add_arguments(self, parser):
        parser.add_argument("--log-dir", type=Path, default=Path(settings.BASE_DIR)/"runtime"/"radar")
        parser.add_argument("--check", action="store_true", help="Print planned commands without starting any process.")
        parser.add_argument("--serve-local", action="store_true", help="Also serve the development UI at 127.0.0.1:8767; not production hosting.")

    def handle(self, *args, **options):
        specs = worker_specs()
        if options["serve_local"]:
            specs["web"] = ["runserver", "127.0.0.1:8767", "--noreload"]
        if options["check"]:
            for name, command in specs.items():
                self.stdout.write(f"{name}: {' '.join(command)}")
            if "session-news" not in specs:
                self.stdout.write("Full Telegram session unavailable; public BeatTheStreet text/caption collection is enabled.")
            return
        owner, children, streams = None, {}, {}
        failures, restart_at = {}, {}
        started_at = {}
        child_launched_at = {}
        launched_at = time.monotonic()
        leases = {"collector": "paper-radar", "evaluator": "radar-evaluator",
                  "delivery": "paper-alerts", "news": "radar-news", "monitor": "radar-monitor"}
        daily_at = None
        daily_retry_at = 0
        root = Path(settings.BASE_DIR)
        try:
            owner = acquire_worker(timezone.now(), name="radar-supervisor")
            options["log_dir"].mkdir(parents=True, exist_ok=True)
            for name in specs:
                streams[name] = (options["log_dir"]/f"{name}.log").open("a", encoding="utf-8")
            while True:
                now = timezone.now()
                heartbeat(owner, "SUPERVISING", "Paper workers only; no broker execution.", now, name="radar-supervisor")
                local = timezone.localtime(now)
                if (daily_preflight_window(local) and daily_at != local.date()
                        and time.monotonic() >= daily_retry_at):
                    # Reference refresh runs in a child so it cannot starve socket collection.
                    process = children.get("preflight")
                    if process is None:
                        if "preflight" not in streams:
                            streams["preflight"] = (options["log_dir"]/"preflight.log").open("a", encoding="utf-8")
                        children["preflight"] = subprocess.Popen(
                            [sys.executable, str(root/"manage.py"), "radar_preflight", "--refresh"],
                            cwd=root, stdout=streams["preflight"], stderr=subprocess.STDOUT,
                        )
                    elif process.poll() is not None:
                        if process.returncode == 0:
                            daily_at = local.date()
                        else:
                            self.stderr.write("Daily preflight failed. See preflight.log; retrying in 15 minutes.")
                            daily_retry_at = time.monotonic()+900
                        children.pop("preflight")
                for name, command in specs.items():
                    process = children.get(name)
                    if process is not None and process.poll() is None and name in leases and time.monotonic()-started_at[name] > 180:
                        worker = RadarWorker.objects.filter(name=leases[name]).first()
                        if child_heartbeat_timed_out(
                            worker,
                            child_launched_at[name],
                            time.monotonic()-started_at[name],
                            now,
                        ):
                            self.stderr.write(f"{name} stopped heartbeating; terminating only its owned PID {process.pid}.")
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=10)
                    if process is not None and process.poll() is not None:
                        failures[name] = min(failures.get(name, 0)+1, 6)
                        restart_at[name] = time.monotonic()+max(95, min(300, 5*2**failures[name]))
                        self.stderr.write(f"{name} exited ({process.returncode}); restart after lease-safe backoff.")
                        children.pop(name)
                    if name in children or time.monotonic() < restart_at.get(name, 0):
                        continue
                    if name == "monitor" and time.monotonic()-launched_at < 90:
                        continue
                    if name == "evaluator" and not RadarWorker.objects.filter(
                        name="paper-radar", lease_until__gt=now, detail__contains="COLLECT_ONLY",
                        heartbeat_at__gte=now-timezone.timedelta(seconds=30),
                    ).exists():
                        continue
                    child_starting = False
                    for child_name, child in children.items():
                        if child_name not in leases or child.poll() is not None:
                            continue
                        worker = RadarWorker.objects.filter(name=leases[child_name]).first()
                        if worker is None or worker.heartbeat_at < child_launched_at[child_name]:
                            child_starting = True
                            break
                    if child_starting:
                        continue
                    children[name] = subprocess.Popen(
                        [sys.executable, str(root/"manage.py"), *command],
                        cwd=root, stdout=streams[name], stderr=subprocess.STDOUT,
                    )
                    started_at[name] = time.monotonic()
                    child_launched_at[name] = now
                    self.stdout.write(f"Started {name} PID {children[name].pid}.")
                close_old_connections()
                time.sleep(10)
        except KeyboardInterrupt:
            self.stdout.write("Stopping owned paper worker processes.")
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        finally:
            for process in children.values():
                if process.poll() is None:
                    process.terminate()
            for process in children.values():
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            for stream in streams.values():
                stream.close()
            if owner:
                RadarWorker.objects.filter(name="radar-supervisor", owner=owner).update(state="STOPPED", lease_until=timezone.now())
