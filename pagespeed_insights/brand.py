"""The Considus mark, the brand faces, and the palette they sit in.

Two pages carry the brand: the setup page and the HTML report. They were about
to hold a copy each of the same font loader, the same icon reader and the same
colour variables, which is the drift this codebase keeps finding in other
people's code and would have been embarrassing to introduce in its own.

BOTH PAGES LOAD NOTHING FROM THE NETWORK. Setup accepts a credential, so it
must not be able to phone anywhere. The report is meant to be emailed and
opened offline by someone who may not be the person who ran the check. Inlining
the faces as data URIs is the only way to keep the brand under either
constraint, and it costs about 155KB, which is the whole reason a report is
170KB rather than 12KB. Worth it: a page that looks like the product it came
from is more likely to be trusted by whoever it lands on.
"""
import base64
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, 'assets')
BRAND_SVG = os.path.join(ASSETS, 'considus-icon.svg')
FONTS_DIR = os.path.join(ASSETS, 'fonts')

# Only the faces a page actually uses. Cormorant Light is display only and
# never appears in a button or a control, per the type rules on considus.com.
_FONT_FACES = [
    ('DM Sans', 400, 'normal', 'dm-sans-normal-400.woff2'),
    ('DM Sans', 500, 'normal', 'dm-sans-normal-500.woff2'),
    ('Cormorant Garamond', 300, 'normal', 'cormorant-garamond-normal-300.woff2'),
    ('Cormorant Garamond', 300, 'italic', 'cormorant-garamond-italic-300.woff2'),
]
_font_css_cache = None

# The considus.com palette, light and dark, as the two pages share it.
PALETTE = """
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
"""

# Header and footer, identical on both pages so they read as one product.
CHROME = """
.brandlink{display:flex;align-items:center;gap:20px;color:inherit;text-decoration:none;width:fit-content}
header svg{width:68px;height:auto;flex:none}
.word{font-family:var(--serif);font-weight:300;font-size:68px;letter-spacing:-0.01em;line-height:1}
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

REPO = 'https://github.com/Considus/pagespeed-insights-mcp'


def font_css():
    """The brand faces as data URIs.

    A missing file drops that face and the text falls back to the system stack,
    which is why this never raises. A page with the wrong font is a small
    problem; a page that will not render is a large one.
    """
    global _font_css_cache
    if _font_css_cache is not None:
        return _font_css_cache
    rules = []
    for family, weight, style, name in _FONT_FACES:
        try:
            with open(os.path.join(FONTS_DIR, name), 'rb') as f:
                encoded = base64.b64encode(f.read()).decode('ascii')
        except OSError:
            continue
        rules.append(
            "@font-face{font-family:'%s';font-weight:%d;font-style:%s;"
            "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2')}"
            % (family, weight, style, encoded))
    _font_css_cache = '\n'.join(rules)
    return _font_css_cache


def mark():
    """The real Considus icon, or nothing rather than an invented one."""
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


def header():
    return f'<header><a class="brandlink" href="https://considus.com">{mark()}' \
           '<span class="word">Considus</span></a></header>'


def footer(extra=''):
    return f'''<footer aria-label="Site footer">
  <div class="fbrand">
    <a class="flogo" href="https://considus.com" aria-label="Considus">{mark()}<span class="fword">Considus</span></a>
    <p class="ftag">Software, considered.</p>
    <p class="fcopy">&copy; 2026 Considus. Apache 2.0 licensed.{extra}</p>
  </div>
  <div class="flinks">
    <a href="https://catchlight.app">Catchlight</a>
    <a href="https://considus.com/privacy">Privacy</a>
    <a href="{REPO}">GitHub</a>
    <a href="https://buymeacoffee.com/considus">Buy me a coffee</a>
    <a href="mailto:hello@considus.com">hello@considus.com</a>
  </div>
</footer>'''
