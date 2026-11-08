"""Central execution policy for broker-facing strategy workers."""

import logging
import os
from dataclasses import dataclass
from enum import StrEnum


logger = logging.getLogger(__name__)

EXECUTION_MODE_ENV = "ARC_EXECUTION_MODE"


class ExecutionMode(StrEnum):
    MANUAL = "manual"
    PREVIEW = "preview"
    LEGACY_AUTO = "legacy_auto"


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: ExecutionMode
    configuration_error: str = ""

    @property
    def allows_automatic_entries(self) -> bool:
        return self.mode is ExecutionMode.LEGACY_AUTO

    @property
    def allows_position_management(self) -> bool:
        return self.mode is not ExecutionMode.PREVIEW


def execution_policy() -> ExecutionPolicy:
    """Return the process execution policy, defaulting safely to manual review."""
    raw_mode = os.getenv(EXECUTION_MODE_ENV, "").strip().lower()
    if not raw_mode:
        return ExecutionPolicy(ExecutionMode.MANUAL)

    try:
        return ExecutionPolicy(ExecutionMode(raw_mode))
    except ValueError:
        allowed = ", ".join(mode.value for mode in ExecutionMode)
        error = (
            f"Invalid {EXECUTION_MODE_ENV}={raw_mode!r}; expected one of {allowed}. "
            "Automatic entries are disabled."
        )
        logger.error(error)
        return ExecutionPolicy(ExecutionMode.MANUAL, configuration_error=error)
