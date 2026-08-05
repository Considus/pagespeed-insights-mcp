"""The terminal face.

    python3 -m pagespeed_insights https://example.com
    python3 -m pagespeed_insights --runs 3 --strategy both https://example.com
    python3 -m pagespeed_insights --field --history https://example.com
    python3 -m pagespeed_insights --lcp https://example.com
    python3 -m pagespeed_insights --compare https://example.com
    python3 -m pagespeed_insights --json https://example.com

EXIT CODES, and why they are not all 1. Something running this in CI needs to
tell "the site is broken" apart from "our quota ran out", because only one of
those should fail a build. So the failure kind is in the exit status:

    0  fine
    1  something else went wrong
    2  bad arguments (argparse)
    3  quota exhausted        — infrastructure, not the site
    4  credential rejected    — configuration, not the site
    5  page unreachable       — THIS one is the site
    6  could not reach Google — network, not the site
"""
import argparse
import json
import pathlib
import sys
import time

from . import __version__, compare, config, crux, lcp, psi, render, report
from .errors import (CredentialRejected, CruxUnavailable, PageSpeedError,
                     PageUnreachable, QuotaExhausted, Unavailable)

EXIT = {QuotaExhausted: 3, CredentialRejected: 4, PageUnreachable: 5, Unavailable: 6}


def _progress(distinct, target, url, strategy, fresh):
    """Only the calls that produced something get a line.

    The MCP face notifies on every poll to hold the client's timeout open. A
    terminal has no timeout to hold open, and a line every 15 seconds saying
    the number has not moved is noise.
    """
    if fresh:
        print(f'  {url} [{strategy}] {distinct}/{target} distinct analyses...',
              file=sys.stderr, flush=True)


def _field(url, key, want_history, quiet):
    """Field data is best-effort. A site with no CrUX data is the normal case,
    not a failure, so nothing here is allowed to change the exit code."""
    out = {}
    try:
        record = crux.record(url, key)
        out['record'] = record
        if not quiet:
            print('\n' + render.crux_record(record, url))
    except CruxUnavailable as e:
        out['record_unavailable'] = {'reason': e.reason, 'message': e.message}
        if not quiet:
            print(f'\n{url}  [real users]\n  {e.message}\n  {e.hint}')
        # Whatever refused the current window will refuse the history too — a
        # missing key, a restricted key, or an origin with no data at all.
        return out

    if want_history:
        try:
            hist = crux.history(url, key)
            out['history'] = hist
            if not quiet:
                print('\n' + render.crux_history(hist, url))
        except CruxUnavailable as e:
            out['history_unavailable'] = {'reason': e.reason, 'message': e.message}
            if not quiet:
                print(f'\n  History unavailable: {e.message}')
    return out


def _lcp(url, key, quiet):
    """The LCP breakdown, from the same record --field would have fetched."""
    try:
        record = crux.record(url, key)
    except CruxUnavailable as e:
        if not quiet:
            print(f'\n{url}  [where the LCP time goes]\n  {e.message}\n  {e.hint}')
        return {'unavailable': {'reason': e.reason, 'message': e.message}}
    analysis = lcp.explain(record)
    if not quiet:
        print('\n' + render.lcp(analysis, url))
        if analysis['available']:
            print('\n  ' + render.LCP_NOTE)
    return analysis


def _compare(res, url, strategy, replace, quiet):
    """Compare this run against the baseline, or record the first one."""
    res['recorded'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    snap = compare.snapshot(res)
    baseline = config.get_baseline(url, strategy)

    if baseline is None or replace:
        config.save_baseline(url, strategy, snap)
        if not quiet:
            print(f'\nBaseline recorded for {url} [{strategy}]'
                  + ('.' if baseline is None else ', replacing the previous one.'))
            print('Make your change, then run this again to see whether it '
                  'moved anything.')
        return {'baseline_recorded': snap}

    result = compare.results(baseline, snap)
    if not quiet:
        print('\n' + render.comparison(result, baseline.get('recorded')))
        print('\n  ' + render.COMPARE_NOTE)
        print('  The baseline is unchanged. Pass --save-baseline to move it '
              'to this measurement.')
    return {'comparison': result, 'baseline': baseline, 'now': snap}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='pagespeed_insights',
        description='PageSpeed Insights, reported with its uncertainty.',
        epilog='A single run is an anecdote. The default of 5 is the point.')
    parser.add_argument('urls', nargs='*',
                        help='URLs to test (default: whatever setup saved)')
    parser.add_argument('--strategy', choices=('mobile', 'desktop', 'both'),
                        default='mobile', help='mobile is what Google ranks on')
    parser.add_argument('--runs', type=int, default=5,
                        help='DISTINCT analyses to collect and median over '
                             '(default 5). Google re-analyses about once a '
                             'minute, so 5 takes roughly 150s')
    parser.add_argument('--interval', type=int, default=psi.POLL_INTERVAL,
                        help=f'seconds between checks (default '
                             f'{psi.POLL_INTERVAL}); below this buys almost '
                             'nothing, a fresh call already blocks 7-12s')
    parser.add_argument('--budget', type=int, default=None,
                        help='seconds to spend per URL before reporting what '
                             'it got (default 60 per analysis, min 120)')
    parser.add_argument('--findings', action='store_true',
                        help='also report what is failing, ranked by what '
                             'fixing it is worth. Costs no extra API calls')
    parser.add_argument('--field', action='store_true',
                        help='also fetch real-user data from the Chrome UX Report')
    parser.add_argument('--lcp', action='store_true',
                        help='also break the LCP into its four phases, from '
                             'real-user data. One extra call, answers at once')
    parser.add_argument('--compare', action='store_true',
                        help='compare this run against the saved baseline for '
                             'the same URL and strategy. The first time, it '
                             'records the baseline instead')
    parser.add_argument('--save-baseline', action='store_true',
                        help='with --compare, replace the baseline with this '
                             'run rather than keeping the original')
    parser.add_argument('--baselines', action='store_true',
                        help='list the saved baselines and exit')
    parser.add_argument('--history', action='store_true',
                        help='with --field, also fetch the p75 time series')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    parser.add_argument('--report', metavar='FILE.html',
                        help='also write a single self-contained HTML page, for '
                             'sending to whoever can act on it. Implies --findings')
    parser.add_argument('--key', help='API key (prefer PAGESPEED_API_KEY or setup)')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)

    if args.baselines:
        held = config.load_baselines()
        if not held:
            print('No baselines. The first --compare on a URL records one.')
        for entry in held.values():
            print(f"{entry.get('url')} [{entry.get('strategy')}]  recorded "
                  f"{entry.get('recorded') or 'unknown'}, "
                  f"{entry.get('analyses')} analyses")
        return 0

    urls = args.urls or config.default_urls()
    if not urls:
        parser.error('no URLs given and none saved. Pass a URL, or run setup.py.')
    if args.compare and args.strategy == 'both':
        parser.error('--compare takes one strategy, since a baseline is per '
                     'strategy. Use --strategy mobile or --strategy desktop.')
    if args.compare and args.runs < 2:
        parser.error('--compare needs at least 2 runs. One analysis has no '
                     'spread, so there is nothing to tell a real change from '
                     'run-to-run noise.')
    if args.save_baseline and not args.compare:
        parser.error('--save-baseline works with --compare.')
    if args.compare:
        # The findings comparison is the useful half of the answer, and it
        # costs no extra API calls.
        args.findings = True
    if args.runs < 1:
        parser.error('--runs must be at least 1')
    if args.history and not args.field:
        parser.error('--history needs --field')
    if args.report:
        # A report with no findings is a scoreboard, and the point of the page
        # is the part somebody can act on.
        args.findings = True

    key = config.api_key(args.key)
    strategies = ('mobile', 'desktop') if args.strategy == 'both' else (args.strategy,)

    if not args.json:
        print('key: ' + ('yes' if key else
                         'NONE — using the shared anonymous quota, which is often\n'
                         '     exhausted. A 429 from here is Google, not your site.'),
              file=sys.stderr)

    payload = {'version': __version__, 'results': [], 'field': {}, 'lcp': {},
               'comparison': {}}
    try:
        for url in urls:
            for strategy in strategies:
                res = psi.measure(url, strategy, args.runs, key,
                                  progress=None if args.json else _progress,
                                  interval=args.interval, budget=args.budget,
                                  with_findings=args.findings)
                payload['results'].append(res)
                if not args.json:
                    print('\n' + render.result(res))
                    if args.findings:
                        print('\n' + render.findings(res.get('findings') or [], url))
                        print('\n  ' + render.FINDINGS_NOTE)
                    for w in res.get('warnings') or []:
                        print(f'\n  Google warns: {w}')
                if args.compare:
                    payload['comparison'][url] = _compare(
                        res, url, strategy, args.save_baseline, args.json)
            if args.field:
                payload['field'][url] = _field(url, key, args.history, args.json)
            if args.lcp:
                payload['lcp'][url] = _lcp(url, key, args.json)
    except PageSpeedError as e:
        if args.json:
            json.dump({'error': e.message, 'hint': e.hint}, sys.stdout, indent=2)
            print()
        else:
            print(f'\nerror: {e.message}\n{e.hint}', file=sys.stderr)
        return EXIT.get(type(e), 1)

    if args.report:
        page = report.build(
            payload['results'],
            field={u: v for u, v in payload['field'].items()},
            findings_by_url={r['url']: r.get('findings') or [] for r in payload['results']},
            generated=time.strftime('%d %B %Y'))
        pathlib.Path(args.report).write_text(page, encoding='utf-8')
        print(f'\nwrote {args.report} ({len(page.encode()) // 1024}KB, self-contained)',
              file=sys.stderr)

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        print('\n' + render.NOISE_NOTE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
