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

from pagespeed_insights import brand, config, crux, psi  # noqa: E402
from pagespeed_insights.errors import PageSpeedError  # noqa: E402

TOKEN = secrets.token_urlsafe(32)
IDLE_TIMEOUT = 900          # the page self-destructs after 15 quiet minutes
SERVER_FILE = os.path.join(HERE, 'mcp_server.py')

_last_seen = time.time()


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
# The mark, the faces and the palette live in pagespeed_insights.brand, because
# the HTML report needs exactly the same ones and two copies of a font loader
# is how the two pages start disagreeing about what the product looks like.
font_css = brand.font_css
brand_mark = brand.mark


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
def path_note():
    """Whether the last line of the POSIX blocks is needed on this machine.

    Setup does not run any of it. It writes nothing outside its own config
    directory, which SECURITY.md makes a rule rather than a habit, and a tool
    that silently drops executables into a PATH directory is exactly the
    behaviour that rule exists to forbid. Same reasoning as never editing an
    assistant's config. So the commands are shown and the reader runs them.

    The check is worth making because the alternative is telling everyone to
    append an export line, including the people who already have one, and a
    duplicated PATH entry is a confusing thing to find later.
    """
    if os.name == 'nt':
        return ('<p class="help">Neither block above applies to Windows and there is '
                'nothing here to install. The launcher they set up needs a symlink, '
                'which wants Developer Mode or an elevated shell, so on Windows you '
                'run it from the folder it is already in, which is what the Windows '
                'block below does.</p>')
    target = os.path.expanduser('~/.local/bin')
    common = ('The first two lines put a <code>pagespeed</code> command into a folder. '
              'The <code>echo</code> line tells your terminal to look in that folder, ')
    # "your terminal" already anchors this to the reader's own machine, and the
    # block above is headed "— this machine". Saying it a third time in two
    # lines was noise.
    if target in (os.environ.get('PATH') or '').split(os.pathsep):
        return (f'<p class="help">{common}which it already does, so you can leave that '
                f'line out. The two above it are the ones that matter.</p>')
    return (f'<p class="help">{common}which it does not do yet, so that is the line '
            f'that makes the command work. It only applies to terminal windows you '
            f'open afterwards, so open a fresh one before deciding it did not '
            f'work.</p>')


def rerun_blocks():
    """Running setup again, in the two forms it actually takes.

    NO `cd`. setup.py resolves everything from its own __file__ and the settings
    live in the platform config directory, so the working directory never
    mattered. Dropping it removes a line and a real trap: in cmd.exe, `cd` to a
    path on another drive silently does nothing, leaving the next line to run in
    the wrong place.

    BOTH PATHS QUOTED. sys.executable on Windows is routinely under
    `C:\\Program Files`, and an unquoted path stops at the space.

    THE POWERSHELL `&`. Once the command itself is quoted, PowerShell needs the
    call operator, because a statement that begins with a quoted string is an
    expression and it prints the path instead of running it. Nothing errors,
    which is what makes it worth its own block rather than a footnote.
    """
    windows = os.name == 'nt'
    sep = '\\' if windows else '/'

    def cmd(for_windows):
        if for_windows == windows:                 # this machine, real paths
            script = html.escape(HERE + sep + 'setup.py')
            exe = html.escape(sys.executable)
            return f'&amp; "{exe}" "{script}"' if windows else f'"{exe}" "{script}"'
        # Another machine, so neither path is knowable. An interpreter taken
        # from PATH needs no quoting and therefore no call operator.
        if for_windows:
            return 'python "path\\to\\pagespeed-insights-mcp\\setup.py"'
        return 'python3 "path/to/pagespeed-insights-mcp/setup.py"'

    # _block owns the "(this machine)" marker. Kept in one place because this
    # function having its own copy is exactly how the two drifted apart.
    return (_block(('darwin', 'linux'), 'macOS and Linux', 'Terminal (zsh or bash)',
                   cmd(False))
            + _block(('nt',), 'Windows', 'PowerShell', cmd(True)))


def _this_os():
    return 'darwin' if sys.platform == 'darwin' else ('nt' if os.name == 'nt' else 'linux')


def _folder(key):
    """The real path for the machine you are on, a placeholder for the others.

    A block headed Windows that says cd "/Users/someone" is worse than no
    example, and the other blocks are by definition about a different computer,
    where this path does not exist anyway.
    """
    if key == _this_os():
        return html.escape(HERE)
    return ('path\\to\\pagespeed-insights-mcp' if key == 'nt'
            else 'path/to/pagespeed-insights-mcp')


def _block(keys, heading, shell, body):
    """One shell block. `keys` is every platform it covers, so the marker still
    appears when macOS and Linux share a block."""
    mark = ' (this machine)' if _this_os() in keys else ''
    return (f'<div class="shell"><h3>{heading}{mark}</h3>'
            f'<div class="blocklabel">Run in {shell}</div>'
            f'<pre class="term">{body}</pre></div>')


def install_blocks():
    """Setting the command up, once, and nothing else.

    Kept apart from the usage examples because they are different kinds of
    thing: this is run once and never again, those are run whenever you want a
    measurement. Stacked in one block with a blank line between them, there was
    no line that was unambiguously the last one, which made the note underneath
    impossible to write without pointing at the wrong command.

    Only macOS and Linux appear. Windows has nothing to install — the symlink
    needs Developer Mode or an elevated shell — and saying so in the note below
    is more honest than inventing a block to keep the set symmetrical.
    """
    def posix(key, profile):
        # ~ rather than the expanded home directory, so the line stays true on
        # whichever machine it is eventually pasted into.
        return (f'mkdir -p ~/.local/bin\n'
                f'ln -s {_folder(key)}/pagespeed ~/.local/bin/pagespeed\n'
                f'echo \'export PATH="$HOME/.local/bin:$PATH"\' &gt;&gt; {profile}')

    return (_block(('darwin',), 'macOS', 'Terminal (zsh)', posix('darwin', '~/.zshrc'))
            + _block(('linux',), 'Linux', 'your terminal (bash)', posix('linux', '~/.bashrc')))


def usage_blocks():
    """Running it, which is two blocks rather than three.

    macOS and Linux are identical here — no profile file is involved, so
    splitting them would be inventing a difference to fill a heading. Windows
    differs for real: no launcher on PATH, so the module is called from the
    folder. Each line runs on its own, because stock PowerShell does not
    understand && between commands.
    """
    return (_block(('darwin', 'linux'), 'macOS and Linux', 'Terminal (zsh or bash)',
                   'pagespeed https://example.com\n'
                   'pagespeed --field --history https://example.com\n'
                   'pagespeed --json https://example.com')
            + _block(('nt',), 'Windows', 'PowerShell',
                     f'cd "{_folder("nt")}"\n'
                     f'python -m pagespeed_insights https://example.com\n'
                     f'python -m pagespeed_insights --field --history https://example.com\n'
                     f'python -m pagespeed_insights --json https://example.com'))


# The point of these is that there is no command to learn and nothing to
# remember. They are what you would have said anyway, which is the whole
# argument for the thing being an MCP server rather than a CLI.
EXAMPLE_PROMPTS = """Check the PageSpeed of https://example.com
How did https://example.com score on mobile and desktop?
Check my saved sites and tell me if anything has regressed
What did real Chrome users experience on https://example.com?
Has https://example.com got slower for real people over the last six months?
Is my PageSpeed setup working?"""


def install_prompt():
    """Self-contained, carries no secret, works with any assistant."""
    return """I have a local MCP server on this computer and I'd like you to register it with \
the MCP client you are running inside.

Server details (the command and args values are JSON strings, quoted and escaped
exactly as a JSON config needs them - copy them as they are):
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
/* Two kinds of block on this page and they are not interchangeable: one gets
   pasted into a chat window, one gets typed at a shell. Identical styling was
   leaving the reader to work out which from the content, so each carries a
   label and its own left edge. */
.blocklabel{font-size:0.68rem;font-weight:500;letter-spacing:.12em;text-transform:uppercase;
            color:var(--muted);margin:22px 0 7px}
pre.paste{border-left:3px solid var(--accent)}
pre.term{border-left:3px solid var(--muted)}
.shell{margin:20px 0 0}
.shell h3{font-family:var(--sans);font-weight:500;font-size:0.85rem;letter-spacing:.04em;
          color:var(--text);margin:0 0 6px}
.shell .blocklabel{margin-top:0}
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
    <a href="https://buymeacoffee.com/considus">Buy me a coffee</a>
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
<!-- Submitting verifies the key against Google before saving, which is two real
     network round trips and several seconds of nothing. Without this the page
     looks dead, and the natural response is to click again and set a second
     verification going. Progressive enhancement: with no JS the form submits
     exactly as before, just without the reassurance. -->
<script>
(function () {{
  var form = document.querySelector('form');
  if (!form) return;
  form.addEventListener('submit', function () {{
    var button = form.querySelector('button');
    if (!button) return;
    button.textContent = 'Checking with Google\\u2026';
    // Disabled AFTER the submit event has been dispatched, so the submission
    // itself is never cancelled by a disabled control.
    setTimeout(function () {{ button.disabled = true; }}, 0);
  }});
}})();
</script>
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
<div class="blocklabel">Paste into your assistant</div>
<pre class="paste">{html.escape(install_prompt())}</pre>
<p class="help">Restart the app afterwards. MCP servers load at startup, so until
   you do, the assistant has no idea it gained anything.</p>

<h2 class="sub">Then just ask</h2>
<p class="help">There is no command and nothing to remember. Once the server is
   registered your assistant has three tools and picks between them from what you
   asked for, so ask in the words you would have used anyway.</p>
<div class="blocklabel">Example prompts</div>
<pre class="paste">{html.escape(EXAMPLE_PROMPTS)}</pre>
<ul>
  <li><b>Lab measurements</b> are the default. Google runs the page on its own
      hardware, so the result is repeatable and comparable, and it is a
      simulation rather than anyone's actual visit.</li>
  <li><b>Real-user data</b> comes from the Chrome UX Report, which is what
      actually happened to people on your site and the only part Google ranks
      on. Ask for real users, or for how something has moved over months, and
      that is what it reaches for.</li>
  <li><b>Something not working</b> gets you the diagnosis instead: whether the
      key is present, whether Google is answering, whether real-user data is
      permitted. Useful for telling a broken setup apart from a slow page.</li>
</ul>
<p class="help">A check takes a few minutes. It is the median of several runs
   rather than one, because a single run disagrees with itself enough to invent
   a regression that was never there.</p>

<h2 class="sub">Changing your saved sites</h2>
<p class="help">The sites above are the default when you do not name one. To add,
   change or remove them, run setup again. The box comes back with what you saved
   already, so edit the list and submit, and empty it entirely to go back to
   naming a site every time. Your key is kept unless you type a new one over it.</p>
{rerun_blocks()}
<p class="help">Both live in <code>{html.escape(str(config.settings_path()))}</code>,
   which you can also edit by hand or delete outright. Deleting it removes the key
   from this machine.</p>

<h2 class="sub">Or use it from a terminal</h2>
<p class="help">It already works from the folder you installed it in, as
   <code>python3 -m pagespeed_insights</code>. For a <code>pagespeed</code> command
   that works from anywhere, link the launcher onto your PATH. Run this yourself,
   setup does not install anything outside its own settings.</p>
{install_blocks()}
{path_note()}

<h2 class="sub">Then run it</h2>
<p class="help">Once, above. From then on, these. Same three tools your assistant
   has, with the measurement printed to the terminal instead.</p>
{usage_blocks()}

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
