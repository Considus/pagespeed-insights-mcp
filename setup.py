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
import base64
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
BRAND_SVG = os.path.join(HERE, 'assets', 'considus-icon.svg')
FONTS_DIR = os.path.join(HERE, 'assets', 'fonts')

_last_seen = time.time()


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
_FONT_FACES = [
    # (family, weight, style, filename) — only the faces the page actually uses.
    ('DM Sans', 400, 'normal', 'dm-sans-normal-400.woff2'),
    ('DM Sans', 500, 'normal', 'dm-sans-normal-500.woff2'),
    ('Cormorant Garamond', 300, 'normal', 'cormorant-garamond-normal-300.woff2'),
    ('Cormorant Garamond', 300, 'italic', 'cormorant-garamond-italic-300.woff2'),
]
_font_css_cache = None


def font_css():
    """Embed the brand faces as data: URIs.

    The page loads nothing from the network, so inlining is the only way to
    guarantee the brand faces on a machine that does not have them installed.
    A missing file drops that face and the text falls back to the system stack,
    which is why this never raises."""
    global _font_css_cache
    if _font_css_cache is not None:
        return _font_css_cache
    rules = []
    for family, weight, style, name in _FONT_FACES:
        try:
            with open(os.path.join(FONTS_DIR, name), 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
        except OSError:
            continue
        rules.append(
            "@font-face{font-family:'%s';font-weight:%d;font-style:%s;"
            "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2')}"
            % (family, weight, style, b64))
    _font_css_cache = '\n'.join(rules)
    return _font_css_cache


def brand_mark():
    """Inline the real Considus icon, or omit it rather than invent one."""
    try:
        with open(BRAND_SVG, 'r', encoding='utf-8') as f:
            svg = f.read()
    except OSError:
        return ''
    start = svg.find('<svg')
    if start < 0:
        return ''
    # Affinity exports an xmlns:serif attribute. It is an XML namespace and is
    # never fetched, but a page that promises to load nothing should not carry
    # a stray http:// at all.
    return (svg[start:]
            .replace(' xmlns:serif="http://www.serif.com/"', '')
            .replace(' xmlns:xlink="http://www.w3.org/1999/xlink"', ''))


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
# Palette and type from considus.com, so setup does not look like a different
# product from the site the user arrived via.
CSS = """
:root{
  --ink:#0F0E0C; --dusk:#1C1A16; --starlight:#ECF1F5; --haze:#9AADB8;
  --slate:#4C5E6B; --cirrus:#EEF3F7;
  --stellar:#A0DCEE; --orbit:#1A9ABE; --anchor:#15788F;
  --bg:var(--cirrus); --surface:#ffffff; --edge:rgba(0,0,0,0.08);
  --text:var(--ink); --muted:var(--slate); --accent:var(--orbit); --cta:var(--anchor);
  --serif:'Cormorant Garamond',Georgia,'Times New Roman',serif;
  --sans:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:var(--ink); --surface:var(--dusk); --edge:rgba(255,255,255,0.06);
         --text:var(--starlight); --muted:var(--haze); --accent:var(--stellar); --cta:var(--orbit); }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
     font-weight:400;font-size:15px;line-height:1.75;letter-spacing:.04em;
     -webkit-font-smoothing:antialiased;padding:48px 24px 80px}
.wrap{max-width:920px;margin:0 auto}
header{margin-bottom:8px}
.brandlink{display:flex;align-items:center;gap:20px;color:inherit;text-decoration:none;width:fit-content}
header svg{width:68px;height:auto;flex:none}
.word{font-family:var(--serif);font-weight:300;font-size:68px;letter-spacing:-0.01em;line-height:1}
h1{font-family:var(--serif);font-weight:300;font-size:40px;line-height:1.15;margin:26px 0 10px}
.lede{color:var(--muted);max-width:62ch;margin:0 0 6px;font-size:0.95rem}
.sub{font-family:var(--sans);font-weight:500;font-size:0.72rem;letter-spacing:.12em;
     text-transform:uppercase;color:var(--muted);margin:40px 0 12px}
.card{background:var(--surface);border:1px solid var(--edge);border-radius:18px;padding:26px}
.row{padding:0 0 14px;margin-bottom:14px;border-bottom:1px solid var(--edge)}
.row:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
label{display:block;font-size:0.68rem;font-weight:500;letter-spacing:.12em;
      text-transform:uppercase;color:var(--muted);margin-bottom:8px;line-height:1.4}
input[type=text],textarea{width:100%;background:transparent;border:none;outline:none;
      color:var(--text);font-family:var(--sans);font-size:1rem;font-weight:400;
      letter-spacing:.04em;padding:3px 0}
/* The textarea gets a visible boundary where the single-line inputs do not.
   Borderless works for an input sitting under its own label, but a 72px
   transparent box reads as empty page and the user never finds the field. */
textarea{min-height:4.5rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         font-size:0.86rem;resize:vertical;border:1px solid var(--edge);
         border-radius:9px;padding:10px 12px;background:var(--bg)}
input::placeholder,textarea::placeholder{color:var(--muted);opacity:.55}
input:focus{border-bottom:1px solid var(--accent);margin-bottom:-1px}
textarea:focus{border-color:var(--accent)}
.help{color:var(--muted);font-size:0.85rem;line-height:1.7;margin:.3rem 0 .6rem}
button{margin-top:28px;background:var(--cta);color:#fff;border:none;border-radius:11px;
       padding:15px 32px;font-family:var(--sans);font-size:0.9rem;font-weight:400;
       letter-spacing:.04em;cursor:pointer}
button:hover{filter:brightness(1.09)}
a{color:var(--accent)}
.err{background:#7f1d1d;color:#fff;border-radius:11px;padding:16px 18px;margin:22px 0;
     font-size:0.9rem;line-height:1.7}
.ok{border-left:3px solid var(--accent);padding-left:18px;margin:22px 0;color:var(--muted);font-size:0.9rem}
.warn{border-left:3px solid #b7791f;padding-left:18px;margin:22px 0;color:var(--muted);font-size:0.9rem}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
     background:var(--bg);border:1px solid var(--edge);border-radius:7px;padding:2px 6px}
pre{overflow-x:auto;white-space:pre-wrap;background:var(--bg);border:1px solid var(--edge);
    border-radius:11px;padding:18px;font-size:0.82rem;line-height:1.6;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
ol{padding-left:20px;color:var(--muted);font-size:0.9rem;max-width:74ch}
ol li{margin:9px 0}
ul{padding-left:20px;color:var(--muted);font-size:0.9rem}
li b{color:var(--text);font-weight:500}
.branch{background:var(--surface);border:1px solid var(--edge);border-left:3px solid var(--accent);
        border-radius:0 11px 11px 0;padding:16px 20px;margin:12px 0 12px -20px;
        color:var(--muted);font-size:0.87rem;line-height:1.7}
.branch b{display:block;color:var(--text);font-weight:500;letter-spacing:.04em;margin-bottom:4px}
footer{border-top:1px solid var(--edge);margin-top:72px;padding-top:36px;
       display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:28px}
.fbrand{display:flex;flex-direction:column;gap:8px}
.flogo{display:flex;align-items:center;gap:8px;color:inherit;text-decoration:none;width:fit-content}
.flogo svg{height:16px;width:auto;flex:none}
.fword{font-family:var(--serif);font-weight:300;font-size:1.05rem;letter-spacing:-0.01em;
       color:var(--muted);line-height:1}
.ftag{font-family:var(--serif);font-style:italic;font-size:0.85rem;color:var(--muted);margin:0}
.fcopy{font-size:0.7rem;color:var(--muted);margin:0}
.flinks{display:flex;flex-direction:column;align-items:flex-end;gap:8px}
.flinks a{font-size:0.78rem;color:var(--muted);text-decoration:none}
.flinks a:hover{color:var(--accent)}
"""


def page(body, title='Considus · PageSpeed Insights setup'):
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s%s</style></head><body><div class="wrap">
<header><a class="brandlink" href="https://considus.com">%s<span class="word">Considus</span></a></header>%s
<footer aria-label="Site footer">
  <div class="fbrand">
    <a class="flogo" href="https://considus.com" aria-label="Considus">%s<span class="fword">Considus</span></a>
    <p class="ftag">Software, considered.</p>
    <p class="fcopy">&copy; 2026 Considus. Apache 2.0 licensed.</p>
  </div>
  <div class="flinks">
    <a href="https://catchlight.app">Catchlight</a>
    <a href="https://considus.com/privacy">Privacy</a>
    <a href="https://github.com/Considus/pagespeed-insights-mcp">GitHub</a>
    <a href="mailto:hello@considus.com">hello@considus.com</a>
  </div>
</footer>
</div></body></html>""" % (html.escape(title), font_css(), CSS,
                           brand_mark(), body, brand_mark())


CONSOLE_STEPS = """
<h2 class="sub">Getting a key, if you do not have one</h2>
<p class="help">About a minute, and it is free. There is one decision to make and it
   comes up at step 3, explained there rather than here.</p>
<ol>
  <li>Open <a href="https://console.cloud.google.com/" target="_blank"
      rel="noopener noreferrer">console.cloud.google.com</a> and pick your account.</li>
  <li><b>APIs &amp; Services</b>.</li>
  <li><b>API Library</b> in the sidebar. Search for <b>PageSpeed Insights API</b>,
      select it, and press <b>Enable</b>.
      <div class="branch">
        <b>While you are on this screen, one decision</b>
        Do you also want real-user data, meaning what actual Chrome visitors
        experienced on your site and how that has moved over the last six months?
        If so, search for <b>Chrome UX Report API</b> and enable that as well
        before you move on. It is the same key and no second credential, just one
        more search on the screen you are already looking at.
        <br><br>
        If you skip it, everything else works and you get lab measurements only.
        Turning it on afterwards means coming back to this screen and to step 7,
        which is the only reason it is worth deciding now rather than later.
      </div></li>
  <li><b>Credentials</b>, <b>from the sidebar, not from the page you are on</b>.
      This is the step that catches people. The button on the page you are
      already on offers only OAuth clients and service accounts, so you conclude
      API keys are not available. They are. The sidebar entry sits just below
      API Library.</li>
  <li><b>Create credentials</b> at the top, then <b>API key</b>.</li>
  <li>Give it a name.</li>
  <li>Under <b>API restrictions</b>, tick <b>PageSpeed Insights API</b>, and tick
      <b>Chrome UX Report API</b> too if you enabled it back at step 3.</li>
  <li>Leave <b>Authenticate API calls through a service account</b> unticked.</li>
  <li>Leave <b>Application restrictions</b> on <b>None</b>. An HTTP-referrer
      restriction looks like the safe choice on that form and makes the key
      unusable from a program like this one, because there is no referring page.
      Step 7 is what limits where the key can reach.</li>
  <li><b>Create</b>, then copy the key.</li>
</ol>
"""


def form_page(error='', existing=None, token=TOKEN):
    have_key = bool(existing and existing.get('api_key'))
    urls = '\n'.join((existing or {}).get('urls') or [])
    banner = (f'<p class="err"><b>That did not work.</b><br>{html.escape(error)}</p>'
              if error else '')
    key_help = ('A key is already saved. Leave this blank to keep it, or paste a new '
                'one to replace it.' if have_key else
                'Starts with <code>AIza</code>. It is stored on this machine only, and '
                'never sent anywhere except to Google.')
    return page(f"""
<h1>PageSpeed Insights</h1>
<p class="lede">Measure a page properly, the median of several runs with the spread
   beside it, so a number arrives with its uncertainty.</p>
{banner}
<form method="post" action="/?t={token}">
  <input type="hidden" name="t" value="{token}">
  <div class="card">
    <div class="row">
      <label for="key">API key</label>
      <input type="text" id="key" name="key" autocomplete="off" spellcheck="false"
             placeholder="{'leave blank to keep the saved key' if have_key else 'AIza...'}">
      <div class="help">{key_help}</div>
    </div>
    <div class="row">
      <label for="urls">Sites you check often, optional</label>
      <textarea id="urls" name="urls" spellcheck="false"
                placeholder="https://example.com/">{html.escape(urls)}</textarea>
      <div class="help">One per line. These become the default when you do not name one.</div>
    </div>
  </div>
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
        link_html = (f'<br><a href="{html.escape(link)}" target="_blank" '
                     f'rel="noopener noreferrer">{html.escape(link)}</a>' if link else '')
        field = ('<p class="warn"><b>Lab measurements only, no real-user data.</b> '
                 'That is the expected result if you skipped the Chrome UX Report '
                 'API at step 3, and nothing is wrong. To add it later, enable '
                 '<b>Chrome UX Report API</b> and tick it in this key\'s API '
                 'restrictions, then run setup again.'
                 f'{link_html}<br><br>If you did enable it just now, give it a '
                 'couple of minutes and re-run setup before changing anything. It '
                 'takes a moment to propagate and until it does it looks exactly '
                 'like being switched off.</p>')

    saved = ('<li>Default sites: ' + ', '.join(html.escape(u) for u in urls) + '</li>'
             if urls else '')
    return page(f"""
<h1>Set up.</h1>
<p class="lede">Your key was checked against Google and works.</p>
<ul>
  <li>Key saved to <code>{html.escape(str(config.settings_path()))}</code></li>
  {saved}
</ul>
{field}

<h2 class="sub">Connect it to your assistant</h2>
<p class="help">Paste this into whichever assistant you want measuring your pages,
   Claude, Cursor, Windsurf, Zed, Codex CLI, VS Code, anything that speaks MCP.
   It carries no key.</p>
<pre>{html.escape(install_prompt())}</pre>
<p class="help">Restart the app afterwards. MCP servers load at startup.</p>

<h2 class="sub">Or use it from a terminal</h2>
<pre>python3 -m pagespeed_insights https://example.com
python3 -m pagespeed_insights --field --history https://example.com</pre>

<p class="help">You can close this tab. Setup has already shut down.</p>
""", 'Considus · PageSpeed Insights, set up')


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
