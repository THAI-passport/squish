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
_KEY_RE = re.compile(r'^SMTP_(\d)_([A-Z_]+)$')
_FIELDS = ("NAME", "SERVER", "PORT", "USERNAME", "PASSWORD", "FROM_NAME", "SECURITY")

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
        profiles.setdefault(idx, {})[field] = value
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
                smtp_lines.append(f"SMTP_{idx}_{field}={prof[field]}")
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
    idx = next(i for i in range(1, MAX_PROFILES + 1) if i not in used)
    profiles[idx] = {
        "NAME": str(data.get("name") or data["username"]).strip(),
        "SERVER": str(data["server"]).strip(),
        "PORT": str(int(data["port"])),
        "USERNAME": str(data["username"]).strip(),
        "PASSWORD": str(data["password"]),
        "FROM_NAME": str(data.get("from_name") or "").strip(),
        "SECURITY": str(data.get("security") or ("ssl" if int(data["port"]) == 465 else "starttls")),
    }
    _write_env_lines(_serialize(profiles))
    return idx


def update_profile_meta(idx: int, name: Optional[str] = None, from_name: Optional[str] = None) -> None:
    """Edit ONLY the nickname / display name -- never the password."""
    profiles = _parse_profiles()
    if idx not in profiles:
        raise KeyError(f"No SMTP profile #{idx}")
    if name is not None:
        profiles[idx]["NAME"] = name.strip()
    if from_name is not None:
        profiles[idx]["FROM_NAME"] = from_name.strip()
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
