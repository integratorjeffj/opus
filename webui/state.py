"""Everything Opus remembers between sessions.

One JSON file under the user's config directory. Not a database, because the
whole of it is a few kilobytes of one person's preferences, and a file she can
open, read, back up and delete is a better fit for a desktop tool than
something she cannot inspect.

WHAT LIVES HERE
    Where her catalogue and exports are, which connectors are configured, the
    release threshold she has chosen, how she has arranged her dashboard, and
    the views she has saved. Nothing about orders or licences -- those belong
    to the ledger, which is the record.

SECRETS
    Passwords and API secrets are held here too, because the alternative for a
    single-user desktop tool is asking her to retype them every session, which
    she will not do. They are stored in a file readable only by her account,
    and the API never sends them back to the browser -- a configured secret
    reads as "********" over the wire. That is honest about what it is: local
    convenience storage, not a vault.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

APP_DIR_NAME = "Opus"
CONFIG_NAME = "config.json"
SCHEMA_VERSION = 1

# Keys whose values must never be sent to the browser.
SECRET_KEYS = ("password", "secret", "client_secret", "smtp_password",
               "api_key", "token")

MASK = "********"


def config_dir():
    """Where a desktop app is expected to keep its settings on this platform."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / APP_DIR_NAME


def config_path():
    return config_dir() / CONFIG_NAME


DEFAULTS = {
    "schema": SCHEMA_VERSION,
    "paths": {
        "catalog_root": "",
        "catalog_map": "",
        "paypal_csv": "",
        "out_dir": "",
        "portal_root": "",
        "watch_folder": "",
    },
    "connectors": {
        # name -> settings. Only what the adapter's configure() accepts.
        "paypal-csv": {},
        "local": {},
        "gdrive": {},
        "paypal-api": {},
        "portal": {"ttl_days": 14, "base_url": ""},
        "smtp": {"port": 587, "use_tls": True, "attach": False},
    },
    "review": {
        # 1.01 holds everything. Automation is opened deliberately, never by
        # default, so this is the one setting that starts at its safest value.
        "hold_below": 1.01,
        "auto_deliver": False,
        "deliver_channels": [],
    },
    "dashboard": {
        # Widget order and visibility on the Overview. Defaults are the layout
        # that reads best before anyone has an opinion.
        "widgets": [
            {"id": "tiles", "visible": True},
            {"id": "attention", "visible": True},
            {"id": "queue", "visible": True},
            {"id": "dropped", "visible": True},
            {"id": "recent", "visible": True},
        ],
        "theme": "system",
        "density": "comfortable",
    },
    "views": [],          # saved filters, each {id, name, workspace, query}
    "publisher": "",
}


def _merge(base, incoming):
    """Deep-merge saved settings over the defaults.

    A setting added in a later version appears with its default rather than
    missing, so an older config file keeps working without a migration step.
    """
    out = dict(base)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load():
    """Read the config, filling in anything missing from the defaults."""
    path = config_path()
    if not path.is_file():
        return json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt config must not stop the app starting. She can always
        # reconfigure; she cannot recover from an app that will not open.
        return json.loads(json.dumps(DEFAULTS))
    return _merge(json.loads(json.dumps(DEFAULTS)), saved)


def save(config):
    """Write the config atomically, and only readable by this user.

    Atomic because a half-written config on a crash would lose every setting;
    0600 because it holds passwords.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, sort_keys=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass                      # best effort; Windows ACLs differ
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def is_secret(key):
    lowered = key.lower()
    return any(s in lowered for s in SECRET_KEYS)


def redact(value):
    """A copy safe to send to the browser: secrets become a mask.

    The mask is deliberately not the real length, so it leaks nothing at all.
    """
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if is_secret(key):
                out[key] = MASK if val else ""
            else:
                out[key] = redact(val)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def apply_update(config, section, incoming):
    """Merge a settings patch, keeping any secret the browser sent back masked.

    The UI round-trips whatever it was given. Without this, opening the
    settings panel and pressing Save would overwrite a real password with the
    literal string "********".
    """
    current = config.get(section) or {}
    merged = _merge(current, incoming or {})

    def unmask(dst, src):
        for key, val in list(dst.items()):
            if isinstance(val, dict) and isinstance(src.get(key), dict):
                unmask(val, src[key])
            elif is_secret(key) and val == MASK:
                dst[key] = (src or {}).get(key, "")
        return dst

    config[section] = unmask(merged, current)
    return config
