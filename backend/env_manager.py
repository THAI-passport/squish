"""Server-side SMTP credential storage in a local .env file.

Optional companion to the client-side Web Crypto vault (backend/static/vault.js).
Stores up to 5 SMTP profiles as SMTP_<n>_* keys in a .env file next to the
backend, so credentials survive across browsers/devices for a single-operator
deployment. Passwords are insert-only from the API's point of view: reads are
always masked, and the only in-place edit allowed is NAME / FROM_NAME.

SECURITY NOTE: these endpoints let a caller read (masked) and write SMTP
credentials that live on this server's disk. app.py gates every route in
this module behind API_KEY -- see the `require_api_key` calls in the
/api/smtp/profiles* handlers. Do not wire these up without that gate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_PROFILES = 5
_KEY_RE = re.compile(r'^SMTP_([1-5])_([A-Z_]+)$')
_FIELDS = ("NAME", "SERVER", "PORT", "USERNAME", "PASSWORD", "FROM_NAME", "SECURITY")
_CONTROL_RE = re.compile(r"[\r\n\x00]")

ENV_PATH = Path(os.environ.get("SMTP_ENV_PATH") or (Path(__file__).parent / ".env"))


def _read_env_lines() -> List[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _write_env_lines(lines: List[str]) -> None:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def _clean_value(value: Any, label: str, *, strip: bool = True) -> str:
    result = str(value)
    if _CONTROL_RE.search(result):
        raise ValueError(f"'{label}' cannot contain CR, LF, or NUL characters")
    return result.strip() if strip else result


def _quote_env(value: str) -> str:
    """Quote managed values for dotenv and shell-style readers."""
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("$", "\\$").replace("`", "\\`"))
    return f'"{escaped}"'


def _unquote_env(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        body = value[1:-1]
        return re.sub(r'\\([\\"$`])', r'\1', body)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _parse_profiles() -> Dict[int, Dict[str, str]]:
    """Read SMTP_<n>_* keys (and any other lines, preserved verbatim)."""
    profiles: Dict[int, Dict[str, str]] = {}
    for line in _read_env_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        m = _KEY_RE.match(key.strip())
        if not m:
            continue
        idx, field = int(m.group(1)), m.group(2)
        profiles.setdefault(idx, {})[field] = _unquote_env(value)
    return profiles


def _serialize(profiles: Dict[int, Dict[str, str]]) -> List[str]:
    """Rewrite the .env file: SMTP_* keys reflect `profiles`, every other
    line (other env vars, comments) is kept as-is, in its original order."""
    other_lines = [
        line for line in _read_env_lines()
        if not (line.strip() and "=" in line and _KEY_RE.match(line.split("=", 1)[0].strip()))
    ]
    smtp_lines: List[str] = []
    for idx in sorted(profiles.keys()):
        prof = profiles[idx]
        for field in _FIELDS:
            if field in prof:
                smtp_lines.append(f"SMTP_{idx}_{field}={_quote_env(prof[field])}")
    return other_lines + (["", "# --- Squish SMTP profiles (managed by env_manager.py) ---"] if smtp_lines else []) + smtp_lines


def list_profiles_masked() -> List[Dict[str, Any]]:
    """Profiles with the password replaced by a mask -- never returned raw."""
    profiles = _parse_profiles()
    out = []
    for idx in sorted(profiles.keys()):
        p = profiles[idx]
        out.append({
            "id": idx,
            "name": p.get("NAME") or p.get("USERNAME") or f"Profile {idx}",
            "server": p.get("SERVER", ""),
            "port": int(p.get("PORT") or 587),
            "username": p.get("USERNAME", ""),
            "password": "••••••••" if p.get("PASSWORD") else "",
            "from_name": p.get("FROM_NAME", ""),
            "security": p.get("SECURITY") or "starttls",
        })
    return out


def get_profile_for_dispatch(idx: int) -> Dict[str, Any]:
    """Raw profile (with plaintext password) for actually sending mail.
    Never returned over the API -- used server-side only."""
    profiles = _parse_profiles()
    p = profiles.get(idx)
    if not p:
        raise KeyError(f"No SMTP profile #{idx}")
    return {
        "server": p.get("SERVER", ""),
        "port": int(p.get("PORT") or 587),
        "username": p.get("USERNAME", ""),
        "password": p.get("PASSWORD", ""),
        "from_name": p.get("FROM_NAME", ""),
        "security": p.get("SECURITY") or "starttls",
    }


def add_profile(data: Dict[str, Any]) -> int:
    """Insert-only: create a new profile. Returns its id (1-5)."""
    profiles = _parse_profiles()
    if len(profiles) >= MAX_PROFILES:
        raise ValueError(f"Maximum of {MAX_PROFILES} SMTP profiles reached")
    for field in ("server", "port", "username", "password"):
        if not data.get(field):
            raise ValueError(f"'{field}' is required")

    used = set(profiles.keys())
    idx = next((i for i in range(1, MAX_PROFILES + 1) if i not in used), None)
    if idx is None:
        raise ValueError(f"Maximum of {MAX_PROFILES} SMTP profiles reached")
    port = int(data["port"])
    if not 1 <= port <= 65535:
        raise ValueError("'port' must be between 1 and 65535")
    security = _clean_value(
        data.get("security") or ("ssl" if port == 465 else "starttls"),
        "security",
    ).lower()
    if security not in {"ssl", "starttls", "none"}:
        raise ValueError("'security' must be ssl, starttls, or none")
    profiles[idx] = {
        "NAME": _clean_value(data.get("name") or data["username"], "name"),
        "SERVER": _clean_value(data["server"], "server"),
        "PORT": str(port),
        "USERNAME": _clean_value(data["username"], "username"),
        "PASSWORD": _clean_value(data["password"], "password", strip=False),
        "FROM_NAME": _clean_value(data.get("from_name") or "", "from_name"),
        "SECURITY": security,
    }
    _write_env_lines(_serialize(profiles))
    return idx


def update_profile_meta(idx: int, name: Optional[str] = None, from_name: Optional[str] = None) -> None:
    """Edit ONLY the nickname / display name -- never the password."""
    profiles = _parse_profiles()
    if idx not in profiles:
        raise KeyError(f"No SMTP profile #{idx}")
    if name is not None:
        profiles[idx]["NAME"] = _clean_value(name, "name")
    if from_name is not None:
        profiles[idx]["FROM_NAME"] = _clean_value(from_name, "from_name")
    _write_env_lines(_serialize(profiles))


def delete_profile(idx: int) -> None:
    profiles = _parse_profiles()
    if idx not in profiles:
        raise KeyError(f"No SMTP profile #{idx}")
    del profiles[idx]
    _write_env_lines(_serialize(profiles))


def wipe_all() -> None:
    """Remove every SMTP_* key from the .env file. Other env vars kept."""
    profiles: Dict[int, Dict[str, str]] = {}
    _write_env_lines(_serialize(profiles))
