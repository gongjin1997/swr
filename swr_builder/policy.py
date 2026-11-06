"""Server-side namespace release policy evaluation."""
from datetime import datetime, time, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo


def deployment_policy_block(policy: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    now = now or datetime.now(timezone.utc)
    freeze = policy.get("freeze") or {}
    if freeze.get("enabled"):
        until = freeze.get("until")
        if not until:
            return "namespace release freeze is enabled"
        try:
            deadline = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return "namespace release freeze has an invalid expiry"
        if deadline.tzinfo is None or now < deadline:
            return "namespace release freeze is active"

    windows = policy.get("deploy_windows") or []
    if not windows:
        return None

    for window in windows:
        try:
            local = now.astimezone(ZoneInfo(window["tz"]))
            current = local.timetz().replace(tzinfo=None)
            start = time.fromisoformat(window["start"])
            end = time.fromisoformat(window["end"])
            days = set(window["days"])
        except (KeyError, TypeError, ValueError):
            return "namespace release window is invalid"

        weekday = local.isoweekday()
        previous_weekday = 7 if weekday == 1 else weekday - 1
        if start < end and weekday in days and start <= current < end:
            return None
        if start > end and ((weekday in days and current >= start) or
                            (previous_weekday in days and current < end)):
            return None

    return "current time is outside the namespace deployment window"
