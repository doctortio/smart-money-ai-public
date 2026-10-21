"""Browser transport for the existing opaque session. Never authorizes a user."""
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import math
import re
import uuid

TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_BROWSER_AGE = 12 * 60 * 60


def bounded_expiry(value, now: float) -> float:
    """Browser cap only. Backend expiration/revocation remains authoritative."""
    try:
        expiry = float(value)
    except (TypeError, ValueError):
        try:
            expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            expiry = now + MAX_BROWSER_AGE
    if not math.isfinite(expiry):
        return now
    return min(expiry, now + MAX_BROWSER_AGE)


def parse_browser_record(record, now: float):
    """Reject malformed input before account lookup; accepted tokens still need RPC validation."""
    if not isinstance(record, dict) or set(record) != {
        "version", "usuario", "auth_token", "auth_binding", "session_token", "expires_at"
    }:
        return None
    if record["version"] != 1:
        return None
    usuario = record["usuario"]
    if not isinstance(usuario, str) or not 3 <= len(usuario) <= 254 or "@" not in usuario:
        return None
    if usuario != usuario.strip().lower():
        return None
    if not all(isinstance(record[k], str) and TOKEN.fullmatch(record[k]) for k in ("auth_token", "auth_binding")):
        return None
    try:
        if str(uuid.UUID(record["session_token"])) != record["session_token"]:
            return None
        expiry = float(record["expires_at"])
        if not math.isfinite(expiry) or not now < expiry <= now + MAX_BROWSER_AGE + 60:
            return None
    except (TypeError, ValueError, AttributeError):
        return None
    return dict(record)


@lru_cache(maxsize=1)
def _component():
    from streamlit.components.v1 import declare_component
    return declare_component("smai_browser_session", path=str(Path(__file__).with_name("session_component")))


def browser_session_command(command):
    return _component()(command=command, key="smai_browser_session", default=None)
