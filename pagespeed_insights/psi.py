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

DEDUPLICATION ON fetchTime. PSI caches its result per URL and replays it. Ask
five times and you can be handed one analysis five times, which turns a median
into a vote for whatever Google has cached. This is not hypothetical: minutes
after a deploy on 2026-07-30, two of three runs came back with a byte-identical
fetchTime from *before* the deploy and dragged the median back to the pre-fix
score while the one fresh run showed the fix. Runs are therefore deduplicated on
fetchTime and the number of genuinely distinct analyses is always reported, so a
"median of 5" that was really a median of 1 says so.
"""
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

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
    """CrUX as PSI embeds it: (scope, metrics). scope says whose data it is.

    `origin` means Google had nothing for this exact URL and answered with the
    whole site's numbers instead. Presenting that as the URL's own data would
    be a lie of omission, so the scope travels with the metrics.
    """
    exp = payload.get('loadingExperience') or {}
    metrics = exp.get('metrics') or {}
    if metrics:
        return ('origin' if exp.get('origin_fallback') else 'url'), metrics
    origin = payload.get('originLoadingExperience') or {}
    if origin.get('metrics'):
        return 'origin', origin['metrics']
    return None, {}


def _spread(values):
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values)}


def summarise(runs, url, strategy, field_scope=None, field_metrics=None):
    """Median and spread across DISTINCT analyses.

    Deduplication happens here rather than at the call site so that nothing can
    reach a median without passing through it.
    """
    seen, unique = set(), []
    for run in runs:
        stamp = run.get('fetchTime')
        if stamp and stamp in seen:
            continue
        if stamp:
            seen.add(stamp)
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


def measure(url, strategy='mobile', runs=5, key=None, progress=None):
    """Run PSI `runs` times and summarise. The whole public entry point.

    progress(done, total, url, strategy) is called after each analysis, so a
    caller that has to keep a client or a person from giving up can say where it
    has got to. A five-run check is minutes, not seconds.
    """
    lab, field_scope, field_metrics = [], None, {}
    for done in range(1, runs + 1):
        payload = fetch(url, strategy, key)
        lhr = payload.get('lighthouseResult') or {}
        runtime_error = lhr.get('runtimeError')
        if runtime_error:
            raise PageUnreachable(
                'Lighthouse could not load the page: '
                f"{runtime_error.get('message', 'no reason given')}",
                'Unlike a quota or credential problem, this one is about the '
                'page itself. Check the URL serves a 200 to an anonymous '
                'visitor, with no login and no geographic block.')
        lab.append(lab_of(lhr))
        if field_scope is None:      # identical across runs; take it once
            field_scope, field_metrics = field_of(payload)
        if progress:
            progress(done, runs, url, strategy)
    return summarise(lab, url, strategy, field_scope, field_metrics)
