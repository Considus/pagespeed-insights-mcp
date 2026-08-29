"""PageSpeed Insights, measured honestly.

This is the whole point of the package, so it is worth being explicit about what
"honestly" costs and why the obvious implementation is wrong.

A Lighthouse run is a simulation on shared hardware. It is noisy. Total Blocking
Time is the worst of them and routinely swings threefold between runs on a page
that has not changed, and the headline performance score is a weighted blend
that inherits every bit of that noise. Ask PSI once and you get a number with no
error bars, and there is no way to tell a real regression from the instrument
moving. Every tool that reports a single run is quietly presenting noise as
measurement.

Two things fix it, and both are here:

MEDIAN OF N, WITH THE SPREAD. Runs are repeated, the median reported, and the
min-max printed beside it. A change that falls inside the spread is not a
change. Quoting the median without the spread would be the same dishonesty in a
smarter hat, so the two travel together everywhere in this module.

DEDUPLICATION, ON TWO RULES. PSI caches its result per URL and replays it. Ask
five times and you can be handed one analysis five times, which turns a median
into a vote for whatever Google has cached. This is not hypothetical: minutes
after a deploy on 2026-07-30, two of three runs came back with a byte-identical
fetchTime from *before* the deploy and dragged the median back to the pre-fix
score while the one fresh run showed the fix.

The first rule is that fetchTime. The second is the measurement itself, and it
exists because the first one leaks. On 2026-08-01, eleven runs against one URL
reported four distinct analyses between them, and two of those four agreed on
FCP to thirteen decimal places — 1686.8756582616209 ms, twice. Two independent
Lighthouse runs do not agree to the femtosecond. It was one analysis served
again under a fetchTime new enough to pass the first rule, and it had been
counted as corroboration.

So a run is also dropped when everything it measured is identical to a run
already seen. That subsumes the first rule almost entirely, since a shared
fetchTime implies shared numbers, and deleting the fetchTime check moves no test
in this suite. It is kept for the one case it still owns alone: a run that
measured nothing has no numbers to compare, so a repeated empty result is
catchable only by its timestamp. Where the two disagree the safer reading wins:
identical numbers
cannot be told apart from a replay, and calling them one analysis understates
the sample instead of overstating the confidence, which is the direction this
module is allowed to be wrong in. The number of genuinely distinct analyses is
always reported, so a "median of 5" that was really a median of 1 says so.
"""
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

from . import findings as _findings
from .errors import (CredentialRejected, PageUnreachable, QuotaExhausted,
                     Unavailable)

API = 'https://www.googleapis.com/pagespeedonline/v5/runPagespeed'
CATEGORIES = ('performance', 'accessibility', 'best-practices', 'seo')

# Lighthouse audit id -> label. The three Core Web Vitals Google actually
# reports, plus the two that explain a bad LCP.
LAB = [
    ('largest-contentful-paint', 'LCP'),
    ('cumulative-layout-shift', 'CLS'),
    ('total-blocking-time', 'TBT'),
    ('first-contentful-paint', 'FCP'),
    ('speed-index', 'Speed Index'),
]

# CrUX keys as PSI embeds them, with the divisor needed to get the real value.
# INP replaced FID as a Core Web Vital in 2024.
#
# CLS IS SCALED BY 100 HERE AND NOWHERE ELSE. PSI reports an embedded CLS
# percentile of 8 to mean a CLS of 0.08 — confirmed by its own histogram, whose
# bins are 0-10 / 10-25 / 25+, the 0.1 and 0.25 thresholds times 100, while the
# LCP bins next to it are plain milliseconds. Miss it and a healthy 0.08 is
# reported as 8.0, which is a catastrophic score, for the one metric where the
# difference between those two numbers is the whole story. The standalone CrUX
# API does NOT do this, so the two sources disagree unless this is applied.
FIELD = [
    ('LARGEST_CONTENTFUL_PAINT_MS', 'LCP', 1),
    ('INTERACTION_TO_NEXT_PAINT', 'INP', 1),
    ('CUMULATIVE_LAYOUT_SHIFT_SCORE', 'CLS', 100),
    ('FIRST_CONTENTFUL_PAINT_MS', 'FCP', 1),
]


def fetch(url, strategy='mobile', key=None, attempts=4, timeout=180):
    """One PSI analysis. Retries 429 and 5xx with backoff; everything else raises."""
    query = urllib.parse.urlencode(
        [('url', url), ('strategy', strategy)] +
        [('category', c) for c in CATEGORIES] +
        ([('key', key)] if key else []))
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(f'{API}?{query}', timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            try:
                msg = json.loads(body)['error']['message']
            except (ValueError, KeyError):
                msg = body[:300]

            if e.code in (429, 500, 502, 503) and attempt < attempts:
                time.sleep(delay)
                delay *= 2
                continue

            if e.code == 429:
                raise QuotaExhausted(
                    'PageSpeed Insights returned 429 (quota exhausted).',
                    'Your project quota is spent for today. It resets at '
                    'midnight Pacific time.' if key else
                    'You are running without an API key, on the shared '
                    'anonymous pool that everyone else is also using and that '
                    'is routinely spent. This says nothing about the site you '
                    'are testing. Run setup to add a key.')
            if e.code in (400, 403):
                raise CredentialRejected(
                    f'PageSpeed Insights rejected the request (HTTP {e.code}): {msg}',
                    'Check the PageSpeed Insights API is enabled on the '
                    'project, and that the key has no HTTP-referrer '
                    'restriction — that makes a key unusable from a script. '
                    'Use API restrictions instead, or none.')
            raise Unavailable(f'PageSpeed Insights returned HTTP {e.code}: {msg}')

        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts:
                time.sleep(delay)
                delay *= 2
                continue
            raise Unavailable(f'Could not reach PageSpeed Insights: {e}',
                              'Check this machine has a working internet connection.')
    raise Unavailable('Ran out of retries talking to PageSpeed Insights.')


def lab_of(lhr):
    """Scores and metrics from one Lighthouse result."""
    out = {'scores': {}, 'metrics': {}, 'fetchTime': lhr.get('fetchTime')}
    for name, cat in (lhr.get('categories') or {}).items():
        # A null score means Lighthouse could not compute the category. Recording
        # it as 0 would drag a median toward a failure that never happened.
        if cat.get('score') is not None:
            out['scores'][name] = cat['score'] * 100
    for audit_id, label in LAB:
        audit = (lhr.get('audits') or {}).get(audit_id) or {}
        if audit.get('numericValue') is not None:
            out['metrics'][label] = audit['numericValue']
    return out


def field_of(payload):
    """CrUX as PSI embeds it: (scope, metrics, subject).

    `scope` says whose data it is. `origin` means Google had nothing for this
    exact URL and answered with the whole site's numbers instead. Presenting
    that as the URL's own data would be a lie of omission, so it travels with
    the metrics.

    `subject` is the URL the data is ACTUALLY about, and it is not always the
    one you asked for. Ask about https://www.bbc.co.uk/ and PSI returns field
    data whose `id` is https://www.bbc.com/, because the request redirects and
    Google reports on where it landed. `origin_fallback` stays false throughout,
    so nothing in the response marks it as a substitution. Verified 2026-08-01,
    and it matters: bbc.co.uk and bbc.com disagree on CLS by two categories, so
    labelling that "this URL" hands someone a real number about a site they did
    not ask about.
    """
    exp = payload.get('loadingExperience') or {}
    metrics = exp.get('metrics') or {}
    if metrics:
        return (('origin' if exp.get('origin_fallback') else 'url'),
                metrics, exp.get('id'))
    origin = payload.get('originLoadingExperience') or {}
    if origin.get('metrics'):
        return 'origin', origin['metrics'], origin.get('id')
    return None, {}, None


def _spread(values):
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values)}


def _fingerprint(run):
    """Everything the run measured, as one comparable value.

    Returns None when the run measured nothing at all. Two empty runs are not
    evidence of a replay, they are two runs that failed, and collapsing them
    would report a smaller sample than was actually attempted.
    """
    body = {'scores': run.get('scores') or {}, 'metrics': run.get('metrics') or {}}
    if not body['scores'] and not body['metrics']:
        return None
    return json.dumps(body, sort_keys=True)


def summarise(runs, url, strategy, field_scope=None, field_metrics=None,
              field_subject=None):
    """Median and spread across DISTINCT analyses.

    Deduplication happens here rather than at the call site so that nothing can
    reach a median without passing through it.
    """
    stamps, measurements, unique = set(), set(), []
    for run in runs:
        stamp = run.get('fetchTime')
        measured = _fingerprint(run)
        if (stamp and stamp in stamps) or (measured and measured in measurements):
            continue
        if stamp:
            stamps.add(stamp)
        if measured:
            measurements.add(measured)
        unique.append(run)

    replays = len(runs) - len(unique)
    result = {
        'url': url,
        'strategy': strategy,
        'analyses': len(unique),
        'requested': len(runs),
        'cached_replays': replays,
        'scores': {},
        'metrics': {},
        'field': {},
        'field_scope': field_scope,
        # The URL the field data is really about. Differs from `url` when the
        # request redirects, which PSI does not otherwise announce.
        'field_subject': field_subject,
    }

    for cat in CATEGORIES:
        values = [r['scores'][cat] for r in unique if cat in r['scores']]
        if values:
            result['scores'][cat] = _spread(values)
    for _, label in LAB:
        values = [r['metrics'][label] for r in unique if label in r['metrics']]
        if values:
            result['metrics'][label] = _spread(values)
    for key, label, divisor in FIELD:
        metric = (field_metrics or {}).get(key)
        if metric and metric.get('percentile') is not None:
            result['field'][label] = {'p75': metric['percentile'] / divisor,
                                      'category': metric.get('category')}
    return result


# Google re-analyses a URL about once a minute and serves the cached result to
# everything that asks in between, so calling N times in a row returns one
# analysis N times. Measured on 2026-08-01 against considus.com: 60 calls at 5s
# produced 9 distinct analyses, one roughly every 60s.
#
# The interval is therefore not a rate limit, it is how often it is worth
# looking. Subsampling that same series showed what each interval would catch:
#
#     5s   60 calls   9 distinct   5 distinct after 136s
#    10s   30 calls   9 distinct   5 distinct after 144s
#    15s   20 calls   8 distinct   5 distinct after 149s   <- default
#    30s   10 calls   7 distinct   5 distinct after 201s
#    60s    6 calls   6 distinct   5 distinct after 244s
#
# Polling three times as hard buys thirteen seconds. Going slower than 30s
# starts losing analyses outright. There is also a floor: a fresh analysis
# BLOCKS for 7-12s while Lighthouse runs, so a request cannot be issued much
# more often than that anyway.
POLL_INTERVAL = 15
# Per analysis asked for, with a floor. Five wants ~150s, so this is generous
# without being unbounded.
BUDGET_PER_ANALYSIS = 60
MIN_BUDGET = 120


def measure(url, strategy='mobile', runs=5, key=None, progress=None,
            interval=POLL_INTERVAL, budget=None, sleep=time.sleep,
            with_findings=False):
    """Collect `runs` DISTINCT analyses, or as many as the budget allows.

    `runs` is a target, not a number of requests. Asking Google five times in a
    row is not five measurements, it is one measurement repeated, and a median
    over it is a median of one dressed up as a median of five. So this keeps
    asking until it has `runs` genuinely different analyses, and reports how
    many it actually got when the budget runs out first.

    Returning fewer than asked for is a normal outcome and not an error. The
    caller is told the real count, which is the only number that makes the
    spread mean anything.

    progress(distinct, target, url, strategy, fresh) fires after EVERY call,
    with `fresh` saying whether that call produced a new distinct analysis.

    Firing on every call rather than only on new analyses keeps the reported
    count moving while a collection runs. Google produces a genuinely new
    analysis only about once a minute, so a callback that fired only on new
    analyses reported at roughly 60s intervals: measured on 2026-08-05, a 2-run
    report emitted its two notifications 58 seconds apart. Polling every 15s
    reports four times inside that window.

    This used to be described as what held a client's request timeout open.
    It is not. Measured 2026-08-29, the clients in use send no progress token
    at all, and Claude Code's timeout is a hard wall that progress does not
    move. Anything longer than about a minute is collected as a background job
    instead, and this callback feeds that job's progress. See mcp.py.

    Callers that count analyses must therefore check `fresh` rather than
    counting calls to this.
    """
    if budget is None:
        budget = max(MIN_BUDGET, runs * BUDGET_PER_ANALYSIS)

    lab, seen = [], set()
    field_scope, field_metrics, field_subject = None, {}, None
    # One compact record per DISTINCT analysis, so a finding can be held to
    # the same standard as a score: reported only if it reproduces.
    finding_records, last_lhr = [], None
    started, calls = time.monotonic(), 0

    while len(lab) < runs:
        call_started = time.monotonic()
        payload = fetch(url, strategy, key)
        calls += 1
        lhr = payload.get('lighthouseResult') or {}
        runtime_error = lhr.get('runtimeError')
        if runtime_error:
            raise PageUnreachable(
                'Lighthouse could not load the page: '
                f"{runtime_error.get('message', 'no reason given')}",
                'Unlike a quota or credential problem, this one is about the '
                'page itself. Check the URL serves a 200 to an anonymous '
                'visitor, with no login and no geographic block.')

        analysis = lab_of(lhr)
        # fetchTime alone is not enough. Google has more than one backend and
        # they hand out the same analysis under different timestamps, so the
        # measurements themselves are what decide whether this is new.
        marks = (analysis.get('fetchTime'), _fingerprint(analysis))
        fresh = not any(m and m in seen for m in marks)
        if fresh:
            seen.update(m for m in marks if m)
            lab.append(analysis)
            if with_findings:
                finding_records.append(_findings.record(lhr))
                last_lhr = lhr
        # EVERY call, not only the ones that produced something new. See the
        # note on the callback below: a heartbeat that only beats when an
        # analysis lands beats about once a minute, which is the timeout it
        # exists to prevent.
        if progress:
            progress(len(lab), runs, url, strategy, fresh)

        if field_scope is None:      # identical across runs; take it once
            field_scope, field_metrics, field_subject = field_of(payload)

        if len(lab) >= runs:
            break
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            break
        # The call itself counts towards the interval. A fresh analysis blocks
        # for 7-12s, so sleeping the full 15 on top would pace at 25s and take
        # half again as long to collect, for nothing.
        wait = interval - (time.monotonic() - call_started)
        if wait > 0:
            sleep(min(wait, remaining))

    result = summarise(lab, url, strategy, field_scope, field_metrics, field_subject)
    result['requested'] = runs
    result['calls'] = calls
    # summarise only ever saw distinct analyses, so its own replay count is
    # zero by construction. The replays happened out here, and they are the
    # whole reason this takes minutes rather than seconds.
    result['cached_replays'] = calls - len(lab)
    result['elapsed'] = round(time.monotonic() - started, 1)
    result['short'] = len(lab) < runs
    if with_findings and last_lhr is not None:
        result['findings'] = _findings.collect(finding_records, last_lhr)
        result['lighthouse_version'] = last_lhr.get('lighthouseVersion')
        # Google's own warnings, verbatim. On a redirect it says so more
        # plainly than anything we would infer from comparing URLs.
        result['warnings'] = last_lhr.get('runWarnings') or []
    return result
