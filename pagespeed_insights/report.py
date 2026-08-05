"""A single HTML page, for the person who has to act on this.

The text report answers someone at a terminal and the JSON answers an
assistant. Neither answers the third reader, who is the one this tool most
needs to serve: somebody who is not technical, or who is and has to hand the
findings to whoever owns the server.

That reader matters because of what the findings turn out to be. The largest
single fault measured on a real site was a DNS redirect, and the next three
were third-party JavaScript. None of those are fixed by the person running the
check. A page they can forward is a better product than a number they cannot
act on alone.

SELF-CONTAINED, on the same principle as the setup page. Inline CSS, no
scripts, no fonts, no images, no network. It opens from a file, survives being
emailed, and works on a machine with no internet. That rules out charting
libraries, so the bars are divs with widths, which is all a bar chart is.

NOTHING IS SAID HERE THAT THE TEXT REPORT DOES NOT SAY. Same data, same
medians, same spreads, same wording for the things that are easy to get wrong.
A prettier page that quietly rounded away a spread would be worse than no page.
"""
import html

from . import brand

CSS = """
/* Verdict colours, the one thing the site palette has no opinion on. */
:root{--good:#2f855a;--fair:#b7791f;--poor:#c53030}
@media (prefers-color-scheme:dark){:root{--good:#68d391;--fair:#f6ad55;--poor:#fc8181}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
     font-size:15px;font-weight:400;line-height:1.65;letter-spacing:.04em;
     padding:48px 24px 80px;-webkit-font-smoothing:antialiased}
.wrap{max-width:920px;margin:0 auto}
/* Cormorant for the page title, as on considus.com and the setup page. It is
   display type only and never appears in a control. */
h1{font-family:var(--serif);font-weight:300;font-size:40px;line-height:1.15;
   margin:26px 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin:0 0 6px}
h2{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;
   color:var(--accent);margin:44px 0 14px}
h3{font-size:15px;font-weight:600;margin:20px 0 4px}
.card{background:var(--surface);border:1px solid var(--edge);border-radius:14px;
      padding:20px 22px;margin:12px 0}
.scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.score{background:var(--surface);border:1px solid var(--edge);border-radius:14px;
       padding:16px 18px;text-align:center}
.score .n{font-size:34px;font-weight:300;line-height:1.1}
.score .l{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin-top:2px}
.score .r{font-size:11px;color:var(--muted);margin-top:6px}
.good{color:var(--good)} .fair{color:var(--fair)} .poor{color:var(--poor)}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
   color:var(--muted);font-weight:600;padding:0 10px 8px 0}
td{padding:7px 10px 7px 0;border-top:1px solid var(--edge);vertical-align:top}
td.n{white-space:nowrap;font-variant-numeric:tabular-nums}
.bar{display:flex;height:7px;border-radius:4px;overflow:hidden;min-width:110px;margin-top:5px}
.bar i{display:block}
.bar .g{background:var(--good)} .bar .f{background:var(--fair)} .bar .p{background:var(--poor)}
.finding{border-top:1px solid var(--edge);padding:14px 0}
.finding:first-of-type{border-top:0}
.finding .t{font-weight:600}
.finding .w{color:var(--accent);font-size:13px;font-variant-numeric:tabular-nums}
.finding .d{color:var(--muted);font-size:13.5px;margin-top:3px}
.tag{display:inline-block;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
     border:1px solid var(--edge);border-radius:99px;padding:1px 8px;color:var(--muted);
     margin-left:6px;vertical-align:1px}
.note{color:var(--muted);font-size:13px;border-left:2px solid var(--edge);
      padding-left:14px;margin:16px 0}
"""


def _score_class(value):
    return 'good' if value >= 90 else 'fair' if value >= 50 else 'poor'


def _bar(hist):
    """The good/fair/poor split as a bar. Three divs, because that is all it is."""
    if not hist or len(hist) < 3:
        return ''
    g, f, p = ((b.get('density') or 0) * 100 for b in hist[:3])
    return (f'<div class="bar"><i class="g" style="width:{g:.1f}%"></i>'
            f'<i class="f" style="width:{f:.1f}%"></i>'
            f'<i class="p" style="width:{p:.1f}%"></i></div>')


def _esc(s):
    return html.escape(str(s), quote=False)


def build(results, field=None, findings_by_url=None, generated='',
          inline_fonts=True):
    """One page for one check. `results` is what measure() returned per URL.

    `inline_fonts` is the difference between a file and a message. The four
    brand faces are 145KB, which is 90% of the page and about 37,000 tokens.
    On disk that is free and the page keeps its typography anywhere it is
    opened. Passed back through an assistant it is most of a context window
    spent on base64, so the MCP asks for them off and the page falls back to
    the system stack. Same renderer either way: a second one would be how the
    two versions start disagreeing about a number.

    The mark stays in both. It is 1.2KB, and a page with no logo does not look
    like it came from anywhere.
    """
    from . import render
    field = field or {}
    findings_by_url = findings_by_url or {}
    out = []

    for res in results:
        url = res['url']
        out.append(f'<h1>{_esc(url)}</h1>')
        analyses = res['analyses']
        word = 'analysis' if analyses == 1 else 'analyses'
        out.append(f'<p class="sub">Median of {analyses} distinct {word}'
                   + (f", {res['calls']} calls over {res['elapsed']:.0f}s" if res.get('calls') else '')
                   + (f' &middot; {_esc(generated)}' if generated else '') + '</p>')

        for warning in res.get('warnings') or []:
            out.append(f'<div class="note"><b>Google warns:</b> {_esc(warning)}</div>')

        out.append('<h2>Lighthouse, simulated</h2><div class="scores">')
        for cat, v in res['scores'].items():
            spread = ('' if v['min'] == v['max']
                      else f"ran {v['min']:.0f}&ndash;{v['max']:.0f}")
            out.append(f'<div class="score"><div class="n {_score_class(v["median"])}">'
                       f'{v["median"]:.0f}</div><div class="l">{_esc(cat)}</div>'
                       f'<div class="r">{spread or "no spread"}</div></div>')
        out.append('</div>')

        if res['metrics']:
            out.append('<div class="card"><table><tr><th>Metric</th><th>Median</th>'
                       '<th>Across analyses</th></tr>')
            for label in ('LCP', 'CLS', 'TBT', 'FCP', 'Speed Index'):
                v = res['metrics'].get(label)
                if not v:
                    continue
                spread = ('&mdash;' if v['min'] == v['max'] else
                          f"{render.duration(label, v['min'])} &ndash; "
                          f"{render.duration(label, v['max'])}")
                out.append(f'<tr><td>{label}</td><td class="n">'
                           f'{render.duration(label, v["median"])}</td>'
                           f'<td class="n">{spread}</td></tr>')
            out.append('</table></div>')
            out.append('<div class="note">The spread is real run-to-run noise on an '
                       'unchanged page. A change smaller than it is not a change.</div>')

        out.append(_field_section(field.get(url), url))
        out.append(_findings_section(findings_by_url.get(url) or []))

    out.append('<div class="note">Generated by '
               '<a href="https://considus.com/pagespeed-insights-mcp/">PageSpeed Insights '
               'MCP</a>, which reports the median of several distinct analyses with its '
               'spread, because a single measurement of a noisy instrument is not a '
               'measurement. Unofficial, and not affiliated with Google LLC.</div>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Page speed report</title>'
            f'<style>{brand.font_css() if inline_fonts else ""}'
            f'{brand.PALETTE}{brand.CHROME}{CSS}</style>'
            f'</head><body><div class="wrap">{brand.header()}{"".join(out)}'
            f'{brand.footer()}</div></body></html>')


def _field_section(entry, url):
    if not entry:
        return ''
    rec = entry.get('record')
    if not rec:
        problem = entry.get('unavailable') or entry.get('record_unavailable') or {}
        if problem.get('reason') == 'no_data':
            return ('<h2>Real users</h2><div class="note">Google publishes no field '
                    'data for this site. That needs enough traffic to keep visitors '
                    'anonymous, so most sites have none. It is not a fault, and it '
                    'means the scores above are a simulation rather than evidence '
                    'about real visitors.</div>')
        return ''

    whose = ('every page on the site' if rec.get('scope') == 'origin' else 'this page')
    out = [f'<h2>Real users &middot; {whose}</h2>',
           '<div class="card"><table><tr><th>Metric</th><th>75th percentile</th>'
           '<th>How visits split</th></tr>']
    from . import render
    for label, v in rec['metrics'].items():
        out.append(f'<tr><td>{label}</td>'
                   f'<td class="n">{render.duration(label, v["p75"])}</td>'
                   f'<td>{_bar(v.get("histogram"))}</td></tr>')
    out.append('</table></div>')

    phases = rec.get('lcp_phases') or {}
    if phases:
        total = sum(v for v in phases.values() if isinstance(v, (int, float))) or 1
        out.append('<h3>Where the LCP time goes</h3><div class="card"><table>')
        for label, ms in phases.items():
            out.append(f'<tr><td>{_esc(label)}</td><td class="n">{ms:.0f} ms</td>'
                       f'<td class="n">{ms / total:.0%}</td></tr>')
        out.append('</table></div>')

    shares = rec.get('shares') or {}
    if shares:
        bits = []
        for label, fractions in shares.items():
            top = ', '.join(f'{_esc(k)} {v:.0%}' for k, v in
                            sorted(fractions.items(), key=lambda kv: -kv[1])[:3] if v >= 0.01)
            bits.append(f'<b>{_esc(label)}:</b> {top}')
        out.append('<div class="note">' + '<br>'.join(bits) + '</div>')
    return ''.join(out)


def _findings_section(items):
    if not items:
        return ''
    from . import render
    groups = {}
    for f in items:
        groups.setdefault(f['category'], []).append(f)

    out = ['<h2>What is failing</h2>']
    for cat in ('performance', 'accessibility', 'best-practices', 'seo'):
        found = groups.get(cat)
        if not found:
            continue
        out.append(f'<h3>{_esc(render.CATEGORY_TITLE.get(cat, cat))}</h3><div class="card">')
        for f in found[:8]:
            tag = ('<span class="tag">not on the page</span>' if f['off_page'] else '')
            worth = render._worth(f)
            out.append(f'<div class="finding"><div class="t">{_esc(f["title"])}{tag}</div>')
            if worth:
                out.append(f'<div class="w">{_esc(worth)}</div>')
            if f['items']:
                out.append(f'<div class="w">{f["items"]} affected element(s)</div>')
            if f['description']:
                out.append(f'<div class="d">{_esc(f["description"])}</div>')
            out.append('</div>')
        if len(found) > 8:
            out.append(f'<div class="finding"><div class="d">'
                       f'and {len(found) - 8} more, lower impact</div></div>')
        out.append('</div>')
    out.append(f'<div class="note">{_esc(render.FINDINGS_NOTE)}</div>')
    return ''.join(out)
