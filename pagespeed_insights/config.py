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


# --------------------------------------------------------------------------
# Where a report may be written
#
# The server writes to two places and no others: its own config directory, and
# a folder the PERSON named. It never picks a folder on someone's behalf and it
# never creates one they did not ask for.
#
# That distinction is the whole guard. An MCP server takes instructions from an
# assistant, and an assistant reads web pages, so "write the report to
# ~/.ssh/authorized_keys" is a sentence that can arrive from a page being
# measured rather than from the user. Confining writes to a folder that already
# exists and was named in the conversation is what keeps that from mattering.
# --------------------------------------------------------------------------

class BadDestination(ValueError):
    """Refusing a place to write, with a reason worth reading."""


def reports_dir():
    """The default, beside the settings. Created on first use, like the rest."""
    d = config_dir() / 'reports'
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(name, fallback):
    """A file name, never a path.

    Rejects rather than sanitises. Quietly turning ../../x.html into x.html
    writes a file somewhere the caller did not intend and says nothing, which
    is the failure this whole package is against.
    """
    if not name:
        return fallback
    if any(sep in name for sep in ('/', '\\')) or name in ('.', '..') or '..' in name:
        raise BadDestination(
            f'{name!r} looks like a path. Pass a file name on its own and use '
            'directory for where it goes.')
    if os.path.isabs(name) or (os.name == 'nt' and ':' in name):
        raise BadDestination(f'{name!r} is an absolute path, not a file name.')
    return name if name.lower().endswith('.html') else name + '.html'


def resolve_destination(directory=None, filename=None, default_name='report.html'):
    """Where to write, as an absolute path that does not already exist.

    `directory` must ALREADY EXIST. Creating one on request means a typo makes
    a stray folder in someone's home directory and the report goes somewhere
    they will not think to look.
    """
    name = safe_filename(filename, default_name)

    if directory:
        target = pathlib.Path(directory).expanduser()
        try:
            target = target.resolve(strict=True)
        except (OSError, FileNotFoundError):
            raise BadDestination(
                f'{directory} does not exist. Give a folder that is already '
                'there, or leave it out and the report goes to '
                f'{reports_dir()}.')
        if not target.is_dir():
            raise BadDestination(f'{target} is a file, not a folder.')
        if not os.access(target, os.W_OK):
            raise BadDestination(f'{target} is not writable.')
    else:
        target = reports_dir()

    path = target / name
    # Never overwrite. Someone comparing a before against an after has two
    # reports they both want, and silently replacing the first is losing the
    # thing they were about to compare against.
    if path.exists():
        stem, suffix = path.stem, path.suffix
        for n in range(2, 1000):
            candidate = target / f'{stem}-{n}{suffix}'
            if not candidate.exists():
                return candidate
        raise BadDestination(f'Too many files named like {name} in {target}.')
    return path


# --------------------------------------------------------------------------
# Baselines
#
# A comparison needs a "before", and the before happened days ago in another
# session. So it is kept on disk, in its own file rather than in settings.json.
# That file holds the API key at 0600, and rewriting it every time a
# measurement lands is a way to eventually lose a key to a half-written file.
# Baselines are not secret and they change often; the key is secret and changes
# almost never. Different lifetimes, different files.
# --------------------------------------------------------------------------

def baselines_path():
    return config_dir() / 'baselines.json'


def _baseline_key(url, strategy):
    # Strategy is part of the identity. A mobile run against a desktop baseline
    # would report the difference between two simulated devices as though it
    # were the effect of a change someone made.
    return f'{strategy}|{url}'


def load_baselines():
    try:
        with open(baselines_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_baseline(url, strategy):
    return load_baselines().get(_baseline_key(url, strategy))


def save_baseline(url, strategy, snapshot):
    """Atomic, like save(), and for the same reason."""
    data = load_baselines()
    data[_baseline_key(url, strategy)] = snapshot
    path = baselines_path()
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return path


def clear_baseline(url, strategy):
    data = load_baselines()
    if data.pop(_baseline_key(url, strategy), None) is None:
        return False
    path = baselines_path()
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return True
