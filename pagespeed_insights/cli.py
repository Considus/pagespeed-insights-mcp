"""The terminal face.

    python3 -m pagespeed_insights https://example.com
    python3 -m pagespeed_insights --runs 3 --strategy both https://example.com
    python3 -m pagespeed_insights --field --history https://example.com
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

from . import __version__, config, crux, psi, render, report
from .errors import (CredentialRejected, CruxUnavailable, PageSpeedError,
                     PageUnreachable, QuotaExhausted, Unavailable)

EXIT = {QuotaExhausted: 3, CredentialRejected: 4, PageUnreachable: 5, Unavailable: 6}


def _progress(distinct, target, url, strategy):
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
    parser.add_argument('--history', action='store_true',
                        help='with --field, also fetch the p75 time series')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    parser.add_argument('--report', metavar='FILE.html',
                        help='also write a single self-contained HTML page, for '
                             'sending to whoever can act on it. Implies --findings')
    parser.add_argument('--key', help='API key (prefer PAGESPEED_API_KEY or setup)')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)

    urls = args.urls or config.default_urls()
    if not urls:
        parser.error('no URLs given and none saved. Pass a URL, or run setup.py.')
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

    payload = {'version': __version__, 'results': [], 'field': {}}
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
            if args.field:
                payload['field'][url] = _field(url, key, args.history, args.json)
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
