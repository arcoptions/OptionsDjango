"""Which commit each host is running, and which one production is serving.

THE PROBLEM THIS SOLVES. Three hosts share this codebase -- a laptop, the Azure
App Service that serves the site, and the VM that places the orders -- and only
the first two were ever connected to anything. Deploying meant pushing, and
updating the VM meant remembering to SSH in and pull. It stopped being
remembered: on 24 August 2026 the VM and origin/master were both eighteen
commits behind the laptop, and the trading engine had been running code five
days older than the site describing it.

THE MARKER IS THE *DEPLOYED* COMMIT, NOT THE HEAD OF MASTER. App Service applies
the migrations, so a commit is only safe for the VM to run once App Service has
started on it -- otherwise the engine reaches for a column that does not exist
yet. `record_deployment` writes this row after `migrate` succeeds, and the VM
follows the row. Pull-based on purpose: no inbound SSH, no deploy key in GitHub,
no NSG change.
"""
import subprocess
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import AppSetting

DEPLOYED_COMMIT_KEY = "deployed_commit"

# Written by the VM's sync timer every run, so the site -- which is a different
# host and cannot ask -- can say what the engine is actually running.
VM_COMMIT_KEY = "vm_commit"

# Set when an update arrived mid-session with a position open, and cleared once
# it has been applied. Its presence is the answer to "why is the VM behind?".
VM_PENDING_KEY = "vm_update_pending"

# The sync timer fires every five minutes. Three missed runs is not a slow tick.
SYNC_STALE_AFTER = 15 * 60

# The workflow stamps this into the package. App Service unpacks a zip with no
# .git in it, so asking git there answers nothing at all.
SHA_FILE = "DEPLOYED_SHA"


def running_commit():
    """The commit this process's code came from, or "" if it cannot be told.

    Two sources, because the two hosts get their code differently: App Service
    reads the file the workflow stamped, the VM is a real checkout and git
    answers directly. Unknown is returned as empty rather than guessed -- a
    wrong commit here would send the VM chasing a deploy that never happened.
    """
    try:
        stamped = (Path(settings.BASE_DIR) / SHA_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    return stamped or git_head()


def git_head(repo=None):
    """`git rev-parse HEAD` where the code lives, or "" if this is not a checkout."""
    try:
        finished = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo or settings.BASE_DIR),
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return finished.stdout.strip() if finished.returncode == 0 else ""


def deployed_commit():
    """The commit App Service last started on, or "" before the first deploy."""
    setting = AppSetting.objects.filter(key=DEPLOYED_COMMIT_KEY).first()
    return str(setting.value if setting else "").strip()


def record_deployed_commit(commit):
    AppSetting.objects.update_or_create(
        key=DEPLOYED_COMMIT_KEY, defaults={"value": commit})


def record_vm_commit(commit):
    """The VM reporting in. Written every sync run, not only when it moves.

    The row's `updated_at` is the only heartbeat the sync timer has: a commit
    that matches and a timestamp from yesterday means the timer died, which
    looks identical to "in sync" if you only compare the two SHAs.
    """
    AppSetting.objects.update_or_create(
        key=VM_COMMIT_KEY, defaults={"value": commit})


def sync_status():
    """Deployed versus running, for the dashboard.

    Read entirely out of the database, because the process rendering this is
    App Service and the host it reports on is the VM. Drift used to be found
    by noticing the engine behaving like older code; this makes it a line on a
    page.
    """
    rows = {row.key: row for row in AppSetting.objects.filter(
        key__in=[DEPLOYED_COMMIT_KEY, VM_COMMIT_KEY, VM_PENDING_KEY])}

    def value(key):
        row = rows.get(key)
        return str(row.value if row else "").strip()

    deployed, vm, pending = value(DEPLOYED_COMMIT_KEY), value(VM_COMMIT_KEY), value(VM_PENDING_KEY)
    reported_at = rows[VM_COMMIT_KEY].updated_at if VM_COMMIT_KEY in rows else None
    age = int((timezone.now() - reported_at).total_seconds()) if reported_at else None
    return {
        "deployed": deployed,
        "deployed_short": short(deployed),
        "vm": vm,
        "vm_short": short(vm),
        "pending": pending,
        "pending_short": short(pending),
        "in_sync": bool(deployed) and deployed == vm,
        "reported_at": reported_at,
        "age_seconds": age,
        "age_minutes": age // 60 if age is not None else None,
        # Heard from, but not lately. Never heard from is a different thing and
        # is told by an empty `vm`.
        "stale": age is not None and age > SYNC_STALE_AFTER,
    }


def short(commit):
    return (commit or "")[:7]
