"""The MCP face — newline-delimited JSON-RPC 2.0 over stdin/stdout.

    python3 -m pagespeed_insights.mcp

Register it with an MCP client and an assistant can measure a page properly
instead of quoting one noisy run at you. It holds no measurement logic: every
tool here calls the same functions the CLI calls and formats with the same
renderer, so the two faces cannot disagree about what a number means.

TWO THINGS THAT BREAK AN MCP SERVER, BOTH GUARDED HERE.

stdout is the protocol. A single stray `print` corrupts the JSON-RPC stream and
the client reports the server dying for no visible reason. Nothing in this
package prints to stdout except this module, and the one function that does
write to stderr (the CLI's progress) is not reachable from here.

Long calls get killed. A five-run check on two URLs is several minutes, which
is past the default timeout of most clients. Clients reset that timeout on
progress notifications, so every run emits one when the caller supplied a
progress token. That is what makes an honest multi-minute measurement possible
at all — without it the only way to answer in time is to run once and quote
noise, which is what everything else does.
"""
import json
import sys
import time

from . import __version__, config, crux, lcp, psi, render, report
from .errors import CruxUnavailable, PageSpeedError

SERVER_NAME = 'pagespeed-insights'
DEFAULT_PROTOCOL = '2025-06-18'

# These keep the worst case to something a client will wait for, and refuse
# clearly rather than starting work that cannot finish.
MAX_RUNS = 10
MAX_URLS = 6
# The cost is TIME, not requests. Google re-analyses a URL about once a minute
# and replays the cached result in between, so five distinct analyses take
# roughly 150 seconds however often you ask. A cap counting requests would have
# been meaningless once collection replaced counting.
MAX_SECONDS = 15 * 60


class ToolError(Exception):
    """A refusal to report as tool output, not a crash."""


def _urls(raw):
    urls = raw or config.default_urls()
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        raise ToolError('No URLs given and none saved. Pass urls, or run setup.')
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise ToolError('urls must be a list of strings.')
    urls = [u.strip() for u in urls if u.strip()]
    for u in urls:
        if not u.startswith(('http://', 'https://')):
            raise ToolError(f'{u!r} is not an http(s) URL. PageSpeed fetches pages '
                            'over the public web; it cannot reach a local path.')
    if len(urls) > MAX_URLS:
        raise ToolError(f'{len(urls)} URLs is more than this server will do in one '
                        f'call (limit {MAX_URLS}). Split it across calls.')
    return urls


def tool_check_pagespeed(args, progress=None):
    urls = _urls(args.get('urls'))
    strategy = args.get('strategy') or 'mobile'
    if strategy not in ('mobile', 'desktop', 'both'):
        raise ToolError("strategy must be 'mobile', 'desktop' or 'both'.")
    strategies = ('mobile', 'desktop') if strategy == 'both' else (strategy,)

    runs = args.get('runs', 5)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 1 to {MAX_RUNS}.')

    # Each URL and strategy collects independently, and each can take up to its
    # own budget. Refuse up front rather than start something the client will
    # abandon halfway through.
    jobs = len(urls) * len(strategies)
    budget = max(psi.MIN_BUDGET, runs * psi.BUDGET_PER_ANALYSIS)
    worst_case = jobs * budget
    if worst_case > MAX_SECONDS:
        raise ToolError(
            f'that is {jobs} page/strategy combinations at up to '
            f'{budget // 60} minutes each, so up to {worst_case // 60} minutes, '
            f'over this server\'s limit of {MAX_SECONDS // 60}. Google re-analyses '
            'a URL about once a minute, so this is time rather than requests and '
            'asking harder will not speed it up. Reduce runs, drop "both", or '
            'check fewer URLs.')

    key = config.api_key()
    target = jobs * runs
    done = [0]

    def tick(distinct, _target, url, strat):
        # Fires when a NEW distinct analysis lands, not per request, so the
        # count reflects measurements collected rather than calls spent.
        done[0] += 1
        if progress:
            progress(done[0], target,
                     f'{url} [{strat}] {distinct}/{runs} distinct analyses')

    reports, results = [], []
    for url in urls:
        for strat in strategies:
            res = psi.measure(url, strat, runs, key, progress=tick)
            results.append(res)
            reports.append(render.result(res))

    payload = {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
               'key_in_use': bool(key), 'results': results}
    return [{'type': 'text', 'text': '\n\n'.join(reports) + '\n\n' + render.NOISE_NOTE},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_diagnose_page(args, progress=None):
    """What is failing, ranked by what fixing it is worth."""
    urls = _urls(args.get('urls'))
    runs = args.get('runs', 3)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 2 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 2 to {MAX_RUNS}. Below '
                        'two there is nothing to check a finding against, so a '
                        'one-off failure would be reported as a fact.')
    key = config.api_key()
    done = [0]

    def tick(distinct, _t, url, strat):
        done[0] += 1
        if progress:
            progress(done[0], len(urls) * runs,
                     f'{url} [{strat}] {distinct}/{runs} distinct analyses')

    texts, payload = [], {}
    for url in urls:
        res = psi.measure(url, 'mobile', runs, key, progress=tick, with_findings=True)
        found = res.get('findings') or []
        texts.append(render.findings(found, url))
        for w in res.get('warnings') or []:
            texts.append(f'  Google warns: {w}')
        payload[url] = {'analyses': res['analyses'], 'findings': found,
                        'lighthouse_version': res.get('lighthouse_version'),
                        'warnings': res.get('warnings') or []}

    return [{'type': 'text', 'text': '\n\n'.join(texts) + '\n\n' + render.FINDINGS_NOTE},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_report(args, progress=None):
    """The whole answer in one call, and the page to send on.

    Three tools existed and a person wanting "how is my site and what should I
    do" had to call all three and stitch the results. That is work this should
    be doing for them.
    """
    urls = _urls(args.get('urls'))
    runs = args.get('runs', 3)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 2 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 2 to {MAX_RUNS}. Below '
                        'two there is nothing to check a finding against.')
    want_html = args.get('html', True)
    key = config.api_key()
    done = [0]

    def tick(distinct, _t, url, strat):
        done[0] += 1
        if progress:
            progress(done[0], len(urls) * runs,
                     f'{url} [{strat}] {distinct}/{runs} distinct analyses')

    texts, results, field, findings_by_url = [], [], {}, {}
    for url in urls:
        res = psi.measure(url, 'mobile', runs, key, progress=tick, with_findings=True)
        results.append(res)
        findings_by_url[url] = res.get('findings') or []
        texts.append(render.result(res))

        try:
            rec = crux.record(url, key)
            field[url] = {'record': rec}
            texts.append(render.crux_record(rec, url))
        except CruxUnavailable as e:
            field[url] = {'unavailable': {'reason': e.reason, 'message': e.message}}
            # Not an error. Most sites have no field data and saying so plainly
            # is the point, because a gap beside a good lab score reads as proof.
            texts.append(f'{url}  [real users]\n  {e.message}\n  {e.hint}')

        texts.append(render.findings(findings_by_url[url], url))
        for warning in res.get('warnings') or []:
            texts.append(f'  Google warns: {warning}')

    blocks = [{'type': 'text',
               'text': '\n\n'.join(texts) + '\n\n' + render.NOISE_NOTE
                       + '\n' + render.FINDINGS_NOTE}]
    if want_html:
        # Fonts off. They are 145KB of base64, 90% of the page and about 37,000
        # tokens, which is most of a context window spent on typography. On disk
        # the CLI keeps them; through here the page falls back to the system
        # stack and costs a few thousand.
        page = report.build(results, field=field, findings_by_url=findings_by_url,
                            generated=time.strftime('%d %B %Y'), inline_fonts=False)
        blocks.append({'type': 'text', 'text': page})
    blocks.append({'type': 'text', 'text': json.dumps(
        {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
         'results': results, 'field': field}, indent=2)})
    return blocks


def tool_field_data(args, progress=None):
    urls = _urls(args.get('urls'))
    want_history = bool(args.get('history'))
    key = config.api_key()

    texts, payload = [], {}
    for url in urls:
        entry = {}
        try:
            rec = crux.record(url, key)
            entry['record'] = rec
            texts.append(render.crux_record(rec, url))
        except CruxUnavailable as e:
            entry['unavailable'] = {'reason': e.reason, 'message': e.message,
                                    'hint': e.hint, 'console_url': e.console_url}
            texts.append(f'{url}  [real users]\n  {e.message}\n  {e.hint}')
            payload[url] = entry
            continue

        if want_history:
            try:
                hist = crux.history(url, key)
                entry['history'] = hist
                texts.append(render.crux_history(hist, url))
            except CruxUnavailable as e:
                entry['history_unavailable'] = {'reason': e.reason,
                                                'message': e.message, 'hint': e.hint}
                texts.append(f'  History unavailable: {e.message}\n  {e.hint}')
        payload[url] = entry

    return [{'type': 'text', 'text': '\n\n'.join(texts)},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_explain_lcp(args, progress=None):
    """Which of four phases owns a slow LCP. Field data, so it answers at once."""
    urls = _urls(args.get('urls'))
    key = config.api_key()

    texts, payload = [], {}
    for url in urls:
        try:
            rec = crux.record(url, key)
        except CruxUnavailable as e:
            payload[url] = {'unavailable': {'reason': e.reason,
                                            'message': e.message, 'hint': e.hint}}
            texts.append(f'{url}  [where the LCP time goes]\n  {e.message}\n  {e.hint}')
            continue
        analysis = lcp.explain(rec)
        payload[url] = analysis
        texts.append(render.lcp(analysis, url))

    # Only when there is a breakdown to caveat. Appended to a "no field data"
    # answer it explains the reading of four numbers nobody was shown.
    body = '\n\n'.join(texts)
    if any(a.get('available') for a in payload.values()):
        body += '\n\n' + render.LCP_NOTE
    return [{'type': 'text', 'text': body},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_diagnose(args, progress=None):
    """What is configured and what actually works, without disclosing the key."""
    key = config.api_key()
    lines = [f'pagespeed-insights-mcp {__version__}',
             f'Settings file: {config.settings_path()}']

    if not key:
        lines += [
            'API key: NONE.',
            '  PageSpeed still answers, on the shared anonymous quota that '
            'everyone else is also using and that is routinely spent. A 429 '
            'from there is Google, not the site being tested.',
            '  The Chrome UX Report needs a key and will refuse entirely.',
            '  Run setup.py to add one.']
        return [{'type': 'text', 'text': '\n'.join(lines)}]

    lines.append('API key: present.')
    try:
        psi.fetch('https://www.google.com/', 'mobile', key, attempts=1, timeout=90)
        lines.append('PageSpeed Insights: working.')
    except PageSpeedError as e:
        lines.append(f'PageSpeed Insights: FAILING — {e.message}')
        if e.hint:
            lines.append(f'  {e.hint}')

    ok, problem = crux.available(key)
    if ok:
        lines.append('Chrome UX Report: working. Real-user data and history are '
                     'available.')
    else:
        lines.append(f'Chrome UX Report: unavailable ({problem.reason}).')
        lines.append(f'  {problem.hint}')
        lines.append('  Everything else works without it.')

    saved = config.default_urls()
    lines.append('Saved URLs: ' + (', '.join(saved) if saved else 'none'))
    return [{'type': 'text', 'text': '\n'.join(lines)}]


TOOLS = [
    {
        'name': 'check_pagespeed',
        'description':
            'Measure a page with Google PageSpeed Insights and report the MEDIAN '
            'of several DISTINCT analyses with the min-max spread, so the number '
            'comes with its uncertainty. A single Lighthouse run is noise, Total '
            'Blocking Time swings threefold between runs on an unchanged page, so '
            'do not set runs=1 to make it fast and then quote the score. Google '
            're-analyses a URL only about once a minute and replays the cached '
            'result in between, so this keeps asking until it has genuinely '
            'different analyses rather than the same one several times. That '
            'means runs=5 takes roughly 150 seconds and asking harder will NOT '
            'speed it up. Reports fewer analyses honestly if time runs out. '
            'Returns a report and the same figures as JSON.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'strategy': {'type': 'string', 'enum': ['mobile', 'desktop', 'both'],
                             'description': 'Default mobile, which Google ranks on.'},
                'runs': {'type': 'integer', 'minimum': 1, 'maximum': MAX_RUNS,
                         'description': 'DISTINCT analyses to collect and median '
                                        'over. Default 5, which takes ~150s.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_check_pagespeed,
    },
    {
        'name': 'report',
        'description':
            'The whole picture for a page in one call: scores with their spread, '
            'real-user data if Google has any, and what is failing ranked by what '
            'fixing it is worth. Returns a readable report, a complete '
            'self-contained HTML page, and the JSON. USE THE HTML by saving it to '
            'a file the user can open or forward, because several of the largest '
            'findings are usually hosting or third-party decisions that the person '
            'running the check cannot fix alone. Set html false if they only want '
            'the answer in conversation. SLOW, several minutes, and costs no extra '
            'API calls over check_pagespeed.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'runs': {'type': 'integer', 'minimum': 2, 'maximum': MAX_RUNS,
                         'description': 'Distinct analyses to collect. Default 3.'},
                'html': {'type': 'boolean',
                         'description': 'Include the HTML page. Default true.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_report,
    },
    {
        'name': 'diagnose_page',
        'description':
            'Report what is FAILING on a page and rank it by what fixing it is '
            'worth, using Google\'s own audit findings and remediation text. '
            'Only reports a fault that failed in EVERY distinct analysis, '
            'because audits are as noisy as scores and a one-off failure is the '
            'instrument moving rather than a fact about the page. Estimated '
            'savings carry their median and spread for the same reason. Ranked '
            'by what actually moves the score: the performance score is five '
            'metrics and every other performance audit weighs zero, so a big '
            'estimated saving on an unweighted diagnostic is not the first thing '
            'to fix. Savings do NOT add up, they overlap, so use the order '
            'rather than the sum. SLOW, like check_pagespeed, and costs no extra '
            'API calls.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'runs': {'type': 'integer', 'minimum': 2, 'maximum': MAX_RUNS,
                         'description': 'Distinct analyses to check findings '
                                        'against. Default 3, minimum 2.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_diagnose_page,
    },
    {
        'name': 'field_data',
        'description':
            'What real Chrome users actually experienced, from the Chrome UX '
            'Report — the only evidence here about real visitors, and the only '
            'thing Google ranks on. Set history for the weekly p75 time series, '
            'which shows whether a site has been getting slower for real people '
            'over months. Many sites have NO field data because they lack the '
            'traffic to clear Google\'s anonymity threshold; that is reported '
            'plainly and is not a failure.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'history': {'type': 'boolean',
                            'description': 'Also fetch the weekly p75 time series.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_field_data,
    },
    {
        'name': 'explain_lcp',
        'description':
            'Break a slow Largest Contentful Paint into the four phases it is '
            'made of: server response, then the delay before the browser starts '
            'fetching the largest image, then the download, then the delay '
            'before it is painted. Turns one number into which part of the load '
            'owns it. FAST, one Chrome UX Report call, no Lighthouse runs and '
            'no noise to average away. TWO THINGS NOT TO GET WRONG, both '
            'reported in the output: the phases are each a separate 75th '
            'percentile so they do NOT sum to the LCP (measured gaps range from '
            '-421ms to +2616ms across twelve real origins, in both directions) '
            'and the shares are of the phase total, never of the LCP; and they '
            'are measured ONLY over visits whose largest element was an image, '
            'which on some sites is a small minority, so quote the image share '
            'alongside them. Needs an API key and real-user data, which many '
            'small sites do not have.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_explain_lcp,
    },
    {
        'name': 'diagnose',
        'description':
            'Report what is configured and what actually works right now — key '
            'present, PageSpeed reachable, Chrome UX Report permitted — without '
            'disclosing the key. Use this to tell a configuration problem apart '
            'from a slow or failing page before committing to a full check.',
        'inputSchema': {'type': 'object', 'properties': {},
                        'additionalProperties': False},
        'handler': tool_diagnose,
    },
]

HANDLERS = {t['name']: t['handler'] for t in TOOLS}
TOOL_DEFS = [{k: t[k] for k in ('name', 'description', 'inputSchema')} for t in TOOLS]


def _send(msg):
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def _result(id_, result):
    _send({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _error(id_, code, message):
    _send({'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}})


def _progress_fn(token):
    if token is None:
        return None

    def emit(done, total, what):
        _send({'jsonrpc': '2.0', 'method': 'notifications/progress',
               'params': {'progressToken': token, 'progress': done, 'total': total,
                          'message': what}})
    return emit


def handle(req):
    method = req.get('method')
    id_ = req.get('id')
    params = req.get('params') or {}

    if method == 'initialize':
        _result(id_, {'protocolVersion': params.get('protocolVersion', DEFAULT_PROTOCOL),
                      'capabilities': {'tools': {}},
                      'serverInfo': {'name': SERVER_NAME, 'version': __version__}})
    elif method in ('notifications/initialized', 'notifications/cancelled'):
        pass
    elif method == 'ping':
        _result(id_, {})
    elif method == 'tools/list':
        _result(id_, {'tools': TOOL_DEFS})
    elif method == 'tools/call':
        name = params.get('name')
        handler = HANDLERS.get(name)
        if handler is None:
            _error(id_, -32602, f'Unknown tool: {name}')
            return
        progress = _progress_fn((params.get('_meta') or {}).get('progressToken'))
        try:
            _result(id_, {'content': handler(params.get('arguments') or {}, progress)})
        except (ToolError, PageSpeedError) as e:
            text = e.message if isinstance(e, PageSpeedError) else str(e)
            hint = getattr(e, 'hint', '')
            _result(id_, {'content': [{'type': 'text',
                                       'text': f'Error: {text}' + (f'\n{hint}' if hint else '')}],
                          'isError': True})
        except Exception as e:
            _result(id_, {'content': [{'type': 'text',
                                       'text': f'Unexpected error in {name}: {e}'}],
                          'isError': True})
    else:
        if id_ is not None:
            _error(id_, -32601, f'Method not found: {method}')


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(req)
        except Exception as e:            # never die on one bad request
            if isinstance(req, dict) and req.get('id') is not None:
                _error(req['id'], -32603, f'Internal error: {e}')


if __name__ == '__main__':
    main()
