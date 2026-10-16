"""Operator-owned tracing configuration, snapshotted in private deployment records.

Installed in Deployment Studio; no project credentials belong in this module.
"""
import copy
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

CONFIG_ENV = "STUDIO_LANGFUSE_CONFIG_FILE"
SNAPSHOT_KEY = "shared_langfuse"
SERVICES = {"backend", "retail-agent", "supplier-agent", "mcp-server"}
REQUIRED = {"LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"}
ALLOWED = REQUIRED | {"LANGFUSE_TRACING_ENABLED", "LANGFUSE_ENVIRONMENT", "LANGFUSE_RELEASE"}


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError()
        data[key] = value
    return data


def validate(values):
    try:
        if not isinstance(values, dict) or set(values) - ALLOWED:
            raise ValueError()
        enabled = values.get("LANGFUSE_TRACING_ENABLED", True)
        if type(enabled) is not bool:
            raise ValueError()
        if not enabled:
            return {"LANGFUSE_TRACING_ENABLED": False}
        if any(not isinstance(values.get(k), str) or not values[k].strip() for k in REQUIRED):
            raise ValueError()
        result = {k: values[k].strip() for k in REQUIRED}
        url = urlsplit(result["LANGFUSE_BASE_URL"])
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or any(c.isspace() for c in result["LANGFUSE_BASE_URL"])):
            raise ValueError()
        url.port
        environment = values.get("LANGFUSE_ENVIRONMENT", "poc")
        if not isinstance(environment, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", environment) or environment.startswith("langfuse"):
            raise ValueError()
        result.update(LANGFUSE_TRACING_ENABLED=True, LANGSMITH_TRACING=False, LANGFUSE_ENVIRONMENT=environment)
        if values.get("LANGFUSE_RELEASE"):
            if not isinstance(values["LANGFUSE_RELEASE"], str) or len(values["LANGFUSE_RELEASE"]) > 100:
                raise ValueError()
            result["LANGFUSE_RELEASE"] = values["LANGFUSE_RELEASE"]
        return result
    except Exception:
        raise ValueError("Shared Langfuse configuration is invalid; contact the dashboard administrator.") from None


def shared_tracing_snapshot():
    """Call only when creating a new run, before any deployment resources exist."""
    path = os.getenv(CONFIG_ENV)
    if not path:
        return {}
    try:
        target = Path(path)
        if target.is_symlink():
            raise ValueError()
        with target.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError()
            if os.name == "posix" and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid not in {0, os.geteuid()}):
                raise ValueError()
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        values = validate(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique))
        # LANGSMITH_TRACING is a derived output, not an allowed input setting.
        values.pop("LANGSMITH_TRACING", None)
        return {SNAPSHOT_KEY: values}
    except Exception:
        raise ValueError("Shared Langfuse configuration is unavailable or invalid; contact the dashboard administrator.") from None


def with_shared_tracing(configurations, private):
    """Use only the saved run snapshot: later VM edits must not alter resumed runs."""
    if SNAPSHOT_KEY not in private:
        return configurations
    settings = validate(private[SNAPSHOT_KEY])
    return {service: (copy.deepcopy(config) | settings if service in SERVICES else copy.deepcopy(config))
            for service, config in configurations.items()}
