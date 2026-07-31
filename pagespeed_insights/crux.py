"""Chrome UX Report — what real Chrome users actually experienced.

Lighthouse is a simulation. CrUX is the only thing here that is evidence about
real visitors, and the only thing Google ranks on. It is also the thing a small
site will not have, because it needs enough traffic to clear an anonymity
threshold. Both facts have to survive into the output or a good lab score gets
mistaken for proof that the site is fast for people.

OPTIONAL, AND DETECTED RATHER THAN CONFIGURED. CrUX takes the same Google Cloud
API key as PageSpeed Insights — one key serves every API enabled on its project
— so this is never a second credential to go and fetch. It is the same key with
one more API switched on. The tool therefore just tries, and lights the feature
up if it works. Nobody is asked to decide anything before they have a basis for
deciding it.

THE ERROR THAT LIES. Verified on 2026-07-31, and the reason this module is more
careful than it looks. With Chrome UX Report API freshly enabled and added to
the key, `queryRecord` returned 200 with full data while `queryHistoryRecord`
returned 403 "Chrome UX Report API has not been used in project N before or it
is disabled" — about the API that had just answered the sibling call. A retry
minutes later succeeded first time with nothing changed. Enablement is not
atomic across endpoints.

A tool that believed that 403 would send the user to the console to enable an
API that was already enabled. They would find it on, retry, be refused again,
and conclude the tool was broken. So `not_enabled` is never reported until it
has been retried, and even then the advice names propagation as a possibility
rather than asserting the API is off.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import CruxUnavailable, Unavailable

API = 'https://chromeuxreport.googleapis.com/v1/records'

# Metric key -> (label, is_a_duration). CrUX reports far more than PSI embeds;
# these are the ones worth showing without turning a report into a spreadsheet.
METRICS = [
    ('largest_contentful_paint', 'LCP', True),
    ('interaction_to_next_paint', 'INP', True),
    ('cumulative_layout_shift', 'CLS', False),
    ('first_contentful_paint', 'FCP', True),
    ('experimental_time_to_first_byte', 'TTFB', True),
    ('round_trip_time', 'RTT', True),
]


def _post(endpoint, body, key, timeout=90):
    request = urllib.request.Request(
        f'{API}:{endpoint}?key={key}',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)


def _classify(code, payload, target):
    """Turn Google's refusal into something the caller can act on."""
    error = payload.get('error') or {}
    message = error.get('message', '')

    if code == 404:
        return CruxUnavailable(
            f'No Chrome UX Report data for {target}.', 'no_data',
            'This is not a failure. Google only publishes field data for '
            'origins with enough traffic to stay anonymous, so a small, new or '
            'pre-launch site will have none. The lab scores are a simulation, '
            'not evidence about real visitors.')

    # Google's own message carries a console link with the project already in
    # it. Surfacing theirs beats hardcoding ours, which would rot.
    console_url = ''
    for word in message.replace('(', ' ').replace(')', ' ').split():
        if word.startswith('https://console.'):
            console_url = word.rstrip('.,')
            break

    if code == 403 and 'has not been used in project' in message:
        return CruxUnavailable(
            'The Chrome UX Report API would not answer.', 'not_enabled',
            'Either it is not enabled for this project, or it was enabled very '
            'recently and has not finished propagating — the two are '
            'indistinguishable from here, and this was already retried. If you '
            'have just enabled it, wait a couple of minutes and run this again '
            'before changing anything.' +
            (f'\nEnable it at: {console_url}' if console_url else ''),
            console_url)

    if code in (401, 403):
        return CruxUnavailable(
            'The API key is not permitted to call the Chrome UX Report API.',
            'restricted',
            'The key exists and works, but its API restrictions do not include '
            'Chrome UX Report API. Add it to the key in the Google Cloud '
            'console, under Credentials, and edit the key\'s API restrictions.',
            console_url)

    return CruxUnavailable(
        f'Chrome UX Report returned HTTP {code}: {message[:200]}', 'error')


def _query(endpoint, body, key, target, propagation_retries=2):
    """One CrUX call, retrying only the refusal that is known to be transient."""
    if not key:
        raise CruxUnavailable(
            'Field data needs an API key.', 'restricted',
            'PageSpeed Insights answers without one, but the Chrome UX Report '
            'API does not. Run setup to add a key.')

    delay = 8
    for attempt in range(propagation_retries + 1):
        try:
            return _post(endpoint, body, key)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode('utf-8', 'replace') or '{}')
            except ValueError:
                payload = {}
            problem = _classify(e.code, payload, target)
            # Only 'not_enabled' is retried. A 404 is a real answer and a
            # restricted key will still be restricted in eight seconds.
            if problem.reason == 'not_enabled' and attempt < propagation_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise problem
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise Unavailable(f'Could not reach the Chrome UX Report API: {e}')


def _target(url, origin_only):
    """CrUX answers about a specific page or a whole origin, and they differ.

    A small site usually has origin-level data and nothing page-level, so
    asking about the page and reporting 'no data' would be wrong when the
    origin has plenty.
    """
    if origin_only:
        parts = urllib.parse.urlsplit(url)
        return {'origin': f'{parts.scheme}://{parts.netloc}'}
    return {'url': url}


def _date(d):
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def _number(value):
    """CrUX returns CLS as a STRING ('0.00') while every other metric is a
    number. Left alone, a caller doing the obvious `cls > 0.1` gets a
    TypeError on Python 3, and only for the one metric where a small value
    matters most. Coerced here, once, so nothing downstream has to know."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def record(url, key, origin_only=True, form_factor=None):
    """Current field data — a 28-day rolling window, p75 per metric."""
    body = _target(url, origin_only)
    if form_factor:
        body['formFactor'] = form_factor
    data = _query('queryRecord', body, key, url)
    rec = data.get('record') or {}
    out = {'scope': 'origin' if origin_only else 'url', 'metrics': {}}
    period = rec.get('collectionPeriod') or {}
    if period:
        out['period'] = {'first': _date(period['firstDate']),
                         'last': _date(period['lastDate'])}
    for metric_key, label, _ in METRICS:
        metric = (rec.get('metrics') or {}).get(metric_key)
        if metric:
            out['metrics'][label] = {
                'p75': _number((metric.get('percentiles') or {}).get('p75')),
                'histogram': metric.get('histogram'),
            }
    return out


def history(url, key, origin_only=True, periods=None, form_factor=None):
    """The p75 time series — weekly windows, about six months of them.

    This is what no lab tool can give you: whether real users have been getting
    a slower site over time, as opposed to whether a simulation on Google's
    hardware scored differently this morning.
    """
    body = _target(url, origin_only)
    if form_factor:
        body['formFactor'] = form_factor
    if periods:
        body['collectionPeriodCount'] = periods
    data = _query('queryHistoryRecord', body, key, url)
    rec = data.get('record') or {}

    out = {'scope': 'origin' if origin_only else 'url', 'periods': [], 'metrics': {}}
    for period in rec.get('collectionPeriods') or []:
        out['periods'].append({'first': _date(period['firstDate']),
                               'last': _date(period['lastDate'])})
    for metric_key, label, _ in METRICS:
        metric = (rec.get('metrics') or {}).get(metric_key)
        if not metric:
            continue
        series = (metric.get('percentilesTimeseries') or {}).get('p75s') or []
        # Google uses null for a period with too little data to publish. Keep
        # the gaps as gaps; interpolating would invent user experiences.
        out['metrics'][label] = [None if v is None else _number(v) for v in series]
    return out


def available(key):
    """Cheap probe for setup: (True, None) or (False, CruxUnavailable).

    Asks about a high-traffic origin on purpose. Probing one of the user's own
    small sites would return 'no data', which says nothing about whether the
    credential works and would report a working setup as broken.
    """
    try:
        record('https://www.google.com', key)
        return True, None
    except CruxUnavailable as e:
        # 'no data' from google.com would be extraordinary, but it would still
        # mean the call itself succeeded.
        return (True, None) if e.reason == 'no_data' else (False, e)
