"""Where the API key and the saved settings live.

STORAGE, AND WHY IT IS A FILE. The Proton Bridge MCP puts its password in the
operating system's credential store and nothing but non-secrets on disk. That is
right for a mailbox password. It is the wrong trade here, and the reason is the
dependency: reaching the credential store portably needs `keyring`, and the
moment this package needs one installed package it needs a virtual environment
and a pip step, which is the difference between "download it and run it" and the
four-command install that turns non-technical people away.

So the key is a file at 0600 in the user's config directory, and this is stated
plainly rather than dressed up. It is proportionate to what the credential is. A
PageSpeed key, restricted to the PageSpeed Insights API as setup instructs, is
read-only access to public measurements of public web pages. It holds no
personal data, unlocks no account, and cannot be billed against — PSI is free.
The worst that a stolen one does is spend a 25,000-a-day quota. That is a
nuisance, not a breach, and it is fixed by deleting the key in the console.

Anyone who would rather keep it in their own secret manager sets
PAGESPEED_API_KEY in the environment instead, which always wins over the file.
"""
import json
import os
import pathlib
import sys

APP = 'pagespeed-insights-mcp'


def config_dir():
    """Platform-conventional, and created 0700 on first use.

    PAGESPEED_CONFIG_DIR overrides it. That exists so the test suite never
    touches a real user's settings, and it earns its keep twice: anyone wanting
    the key somewhere specific — a per-project directory, an encrypted volume —
    sets it and everything follows.
    """
    override = os.environ.get('PAGESPEED_CONFIG_DIR', '').strip()
    if override:
        d = pathlib.Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass
        return d
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or (pathlib.Path.home() / 'AppData' / 'Roaming')
    elif sys.platform == 'darwin':
        base = pathlib.Path.home() / 'Library' / 'Application Support'
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or (pathlib.Path.home() / '.config')
    d = pathlib.Path(base) / APP
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def settings_path():
    return config_dir() / 'settings.json'


def load():
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(values):
    """Atomic, and 0600 before anything is written into it.

    Written to a temp file in the same directory and renamed, so a crash or a
    full disk leaves the previous settings intact rather than a half-file that
    parses as empty and looks like the user was never set up.

    WINDOWS. The 0600 is a no-op there. Windows has no POSIX file modes, so the
    mode argument is ignored and the file reports 0666. Protection instead comes
    from the directory it lives in: %APPDATA% sits inside the user's profile,
    which is ACL'd to that user by default. That is a real protection and a
    weaker promise than the one POSIX gives, because it is inherited rather than
    set, and a user who has loosened their profile ACLs does not get it. Said
    plainly in the README rather than papered over, and tested for what each
    platform actually guarantees rather than asserted uniformly."""
    path = settings_path()
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(values, f, indent=2)
    os.replace(tmp, path)
    return path


def api_key(explicit=None):
    """The key, or None for the keyless shared pool.

    Order: explicit argument, PAGESPEED_API_KEY, settings file. Environment
    beats the file so a client config or a secret manager can override without
    rewriting anything.

    Returning None is a supported state, not a failure. PSI answers without a
    key, on a shared anonymous quota that other people routinely exhaust, which
    makes it fine for trying the tool and useless for relying on it. The 429
    that follows is where the tool explains that.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get('PAGESPEED_API_KEY', '').strip()
    if env:
        return env
    stored = (load().get('api_key') or '').strip()
    return stored or None


def default_urls():
    return list(load().get('urls') or [])
