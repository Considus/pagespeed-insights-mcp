#!/usr/bin/env python3
"""Guided setup.

    python3 setup.py

Opens a small page in your own browser, served from this machine on a random
port behind a single-use link. It takes your API key, checks it actually works
before saving anything, tells you whether real-user data is available, and hands
you a prompt that installs the server into whichever assistant you use. Then it
shuts itself down.

Nothing leaves this computer except the two calls made to Google to verify the
key, and those are the same calls the tool makes in normal use.

WHY A BROWSER PAGE AND NOT QUESTIONS IN THE TERMINAL. The people this is for
have just come from a Google Cloud console, holding a long string they need to
paste. Pasting into a terminal prompt is where that goes wrong — no correction,
no visible field, and on Windows a right-click paste that may or may not work. A
form field is the thing everyone already knows how to use.

WHY IT NEVER EDITS YOUR ASSISTANT'S CONFIG. Every client keeps its MCP settings
somewhere different, under a different key, and those locations move. Shipping a
list of paths means shipping something that quietly rots. Your assistant already
knows where its own config lives, so setup hands you a prompt to give it. One
path that works for every client, including ones that did not exist when this
was written.
"""
import hmac
import html
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pagespeed_insights import config, crux, psi  # noqa: E402
from pagespeed_insights.errors import PageSpeedError  # noqa: E402

TOKEN = secrets.token_urlsafe(32)
IDLE_TIMEOUT = 900          # the page self-destructs after 15 quiet minutes
SERVER_FILE = os.path.join(HERE, 'mcp_server.py')

_last_seen = time.time()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(key):
    """Prove the key works before saving it. Returns (ok, message, crux_state).

    Setup that saves an unverified credential is setup that fails later, in
    another program, with an error the user cannot connect to anything they
    did. One real call now is worth the wait.
    """
    try:
        psi.fetch('https://www.google.com/', 'mobile', key, attempts=1, timeout=90)
    except PageSpeedError as e:
        return False, f'{e.message}\n{e.hint}', None

    ok, problem = crux.available(key)
    if ok:
        return True, '', {'available': True}
    return True, '', {'available': False, 'reason': problem.reason,
                      'hint': problem.hint, 'console_url': problem.console_url}


# ---------------------------------------------------------------------------
# The prompt that installs the server
# ---------------------------------------------------------------------------
def install_prompt():
    """Self-contained, carries no secret, works with any assistant."""
    return """I have a local MCP server on this computer and I'd like you to register it with \
the MCP client you are running inside.

Server details (the command and args values are JSON strings, quoted and escaped
exactly as a JSON config needs them — copy them as they are):
  name    = "pagespeed-insights"
  command = %s
  args    = [%s]

Please:
1. Work out where THIS client stores its MCP server configuration on this machine.
2. Make a backup of that file before changing it.
3. Add the server above using whichever key this client expects, for example:
   - "mcpServers"      (Claude Code, Claude Desktop, Cursor, Windsurf, Gemini CLI)
   - "servers"         (VS Code / GitHub Copilot agent mode)
   - "context_servers" (Zed)
   - a [mcp_servers.pagespeed-insights] section for TOML configs (OpenAI Codex CLI)
4. No environment variables or secrets are needed. The server reads its own settings \
file. Never put an API key in the config.
5. Tell me which file you changed and whether I need to restart the app.

If you cannot write files, just tell me the exact file path and the exact snippet to \
paste, and I will do it myself.""" % (json.dumps(sys.executable), json.dumps(SERVER_FILE))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 41rem; margin: 0 auto; padding: 3rem 1.5rem 5rem; }
h1 { font-size: 1.5rem; font-weight: 500; margin: 0 0 .4rem; }
h2 { font-size: 1rem; font-weight: 600; margin: 2.2rem 0 .6rem; }
p, li { opacity: .85; }
.lede { opacity: .7; margin: 0 0 2rem; }
label { display: block; font-weight: 600; margin: 1.4rem 0 .3rem; }
.help { font-size: .87rem; opacity: .65; margin: .2rem 0 .5rem; }
input[type=text], textarea { width: 100%; padding: .6rem .7rem; font: inherit;
       border: 1px solid rgba(128,128,128,.45); border-radius: 6px;
       background: transparent; color: inherit; }
textarea { min-height: 5.5rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: .9rem; }
button { margin-top: 1.8rem; padding: .65rem 1.5rem; font: inherit; font-weight: 600;
       border: 0; border-radius: 6px; background: #2b6cb0; color: #fff; cursor: pointer; }
pre { padding: 1rem; border: 1px solid rgba(128,128,128,.35); border-radius: 6px;
       overflow-x: auto; white-space: pre-wrap; font-size: .85rem; }
.bad { border-left: 3px solid #c53030; padding-left: .9rem; }
.warn { border-left: 3px solid #b7791f; padding-left: .9rem; }
.ok { border-left: 3px solid #2f855a; padding-left: .9rem; }
ol { padding-left: 1.2rem; } ol li { margin: .35rem 0; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .88em; }
"""


def page(body, title='PageSpeed Insights MCP — setup'):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>')


CONSOLE_STEPS = """
<h2>Getting a key, if you don't have one</h2>
<ol>
  <li>Open <code>console.cloud.google.com</code> and pick your account.</li>
  <li><b>APIs &amp; Services</b>.</li>
  <li><b>API Library</b> in the sidebar. Search for <b>PageSpeed Insights API</b>,
      select it, and press <b>Enable</b>.</li>
  <li><b>Credentials</b> — <b>from the sidebar, not from the page you're on</b>.
      This is the step everyone gets wrong: the button on the page offers only
      OAuth clients and service accounts, and you will conclude API keys don't
      exist. They do. The sidebar entry sits just below API Library.</li>
  <li><b>Create credentials</b> at the top, then <b>API key</b>.</li>
  <li>Give it a name.</li>
  <li>Under <b>API restrictions</b>, tick <b>PageSpeed Insights API</b>.</li>
  <li>Leave <b>Authenticate API calls through a service account</b> unticked.</li>
  <li>Leave <b>Application restrictions</b> on <b>None</b>. An HTTP-referrer
      restriction makes a key unusable from a program like this one.</li>
  <li><b>Create</b>, then copy the key.</li>
</ol>
<p class="help">Optional: repeat step 3 for <b>Chrome UX Report API</b>, and add it
   at step 7 too. That unlocks real-user data and its history. Everything else
   works without it.</p>
"""


def form_page(error='', existing=None, token=TOKEN):
    have_key = bool(existing and existing.get('api_key'))
    urls = '\n'.join((existing or {}).get('urls') or [])
    banner = f'<p class="bad"><b>That didn\'t work.</b><br>{html.escape(error)}</p>' if error else ''
    key_help = ('A key is already saved. Leave this blank to keep it, or paste a '
                'new one to replace it.' if have_key else
                'Starts with <code>AIza</code>. It is stored on this machine only.')
    return page(f"""
<h1>PageSpeed Insights MCP</h1>
<p class="lede">Measure a page properly — the median of several runs, with the spread,
   so a number arrives with its uncertainty.</p>
{banner}
<form method="post" action="/?t={token}">
  <input type="hidden" name="t" value="{token}">
  <label for="key">API key</label>
  <div class="help">{key_help}</div>
  <input type="text" id="key" name="key" autocomplete="off" spellcheck="false"
         placeholder="{'leave blank to keep the saved key' if have_key else 'AIza...'}">

  <label for="urls">Sites you check often <span style="font-weight:400">(optional)</span></label>
  <div class="help">One URL per line. These become the default when you don't name one.</div>
  <textarea id="urls" name="urls" spellcheck="false"
            placeholder="https://example.com/">{html.escape(urls)}</textarea>

  <button type="submit">Check the key and save</button>
  <p class="help">This makes two real calls to Google, so it takes a few seconds.</p>
</form>
{CONSOLE_STEPS}
""")


def done_page(urls, crux_state):
    if crux_state and crux_state.get('available'):
        field = ('<p class="ok"><b>Real-user data is available.</b> The Chrome UX '
                 'Report answered, so you get field data and its history as well as '
                 'lab scores.</p>')
    else:
        link = (crux_state or {}).get('console_url') or ''
        link_html = (f'<br><a href="{html.escape(link)}">{html.escape(link)}</a>'
                     if link else '')
        field = ('<p class="warn"><b>Real-user data is not available yet.</b> '
                 'Everything else works. To turn it on, enable <b>Chrome UX Report '
                 'API</b> and add it to this key\'s API restrictions.'
                 f'{link_html}<br><span class="help">If you just enabled it, give it '
                 'a couple of minutes and run this setup again — it takes a moment to '
                 'propagate, and until it does it looks exactly like being off.</span></p>')

    saved = ('<li>Default sites: ' + ', '.join(html.escape(u) for u in urls) + '</li>'
             if urls else '')
    return page(f"""
<h1>Set up.</h1>
<p class="lede">Your key was checked against Google and works.</p>
<ul>
  <li>Key saved to <code>{html.escape(str(config.settings_path()))}</code>, readable
      only by you.</li>
  {saved}
</ul>
{field}

<h2>Connect it to your assistant</h2>
<p>Paste this into whichever assistant you want to use it from — Claude, Cursor,
   Windsurf, Zed, Codex CLI, VS Code, anything that speaks MCP. It carries no key.</p>
<pre>{html.escape(install_prompt())}</pre>
<p class="help">Restart the app afterwards. MCP servers load at startup.</p>

<h2>Or use it from a terminal</h2>
<pre>python3 -m pagespeed_insights https://example.com
python3 -m pagespeed_insights --field --history https://example.com</pre>

<p class="help">You can close this tab. Setup has already shut down.</p>
""", 'Set up — PageSpeed Insights MCP')


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'pagespeed-setup'

    def log_message(self, *_args):
        """Silence. The access log would record nothing useful and this process
        has no business writing down what the user typed."""

    def _authorised(self):
        from urllib.parse import parse_qs, urlparse
        given = (parse_qs(urlparse(self.path).query).get('t') or [''])[0]
        return hmac.compare_digest(given, TOKEN)

    def _reply(self, body, status=200):
        payload = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        global _last_seen
        _last_seen = time.time()
        if not self._authorised():
            self._reply(page('<h1>Not this link</h1><p>Run setup again to get a '
                             'fresh one.</p>'), 403)
            return
        self._reply(form_page(existing=config.load()))

    def do_POST(self):
        global _last_seen
        _last_seen = time.time()
        if not self._authorised():
            self._reply(page('<h1>Not this link</h1>'), 403)
            return

        from urllib.parse import parse_qs
        length = int(self.headers.get('Content-Length') or 0)
        fields = parse_qs(self.rfile.read(length).decode('utf-8'))
        if not hmac.compare_digest((fields.get('t') or [''])[0], TOKEN):
            self._reply(page('<h1>Not this link</h1>'), 403)
            return

        existing = config.load()
        key = (fields.get('key') or [''])[0].strip() or (existing.get('api_key') or '')
        urls = [u.strip() for u in (fields.get('urls') or [''])[0].splitlines() if u.strip()]

        if not key:
            self._reply(form_page('No key given, and none saved yet.', existing))
            return
        bad = [u for u in urls if not u.startswith(('http://', 'https://'))]
        if bad:
            self._reply(form_page(f'These need to start with http:// or https:// — '
                                  f'{", ".join(bad)}', existing))
            return

        ok, message, crux_state = verify(key)
        if not ok:
            self._reply(form_page(message, existing))
            return

        config.save({'api_key': key, 'urls': urls})
        self._reply(done_page(urls, crux_state))
        threading.Thread(target=self._shutdown, daemon=True).start()

    def _shutdown(self):
        time.sleep(1.0)          # let the response finish going out
        self.server.shutdown()


def _idle_watch(server):
    while True:
        time.sleep(5)
        if time.time() - _last_seen > IDLE_TIMEOUT:
            print('\nSetup timed out and shut down. Run it again when you\'re ready.')
            server.shutdown()
            return


def main():
    with socket.socket() as probe:          # let the OS pick a free port
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]

    # 127.0.0.1 only. Never 0.0.0.0 — nothing on the network has any business
    # reaching a page that accepts a credential.
    server = http.server.HTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}/?t={TOKEN}'

    print('PageSpeed Insights MCP — setup\n')
    print('Open this in your browser if it does not open by itself:\n')
    print(f'  {url}\n')
    print(f'The link works once, for this run only, and expires after '
          f'{IDLE_TIMEOUT // 60} quiet minutes.')
    # Explicit: Python block-buffers stdout when it is not a terminal, so
    # anyone piping or logging this would otherwise see the link only after the
    # server had already stopped.
    sys.stdout.flush()

    threading.Thread(target=_idle_watch, args=(server,), daemon=True).start()
    try:
        webbrowser.open(url)
    except Exception:
        pass                                 # the printed link is the real path

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped. Nothing was saved.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
