"""Which of four phases owns a slow Largest Contentful Paint.

LCP on its own is a number with no handle on it. Chrome UX Report publishes it
decomposed into four sequential phases, and that turns "LCP is 2.4s" into which
part of the load is responsible, which is the difference between a hosting
problem and a lazy-load attribute.

It is entirely field data, so it answers in a second and has no run-to-run noise
to average away. What it has instead are two traps, both measured rather than
reasoned about, and both of which produce a confident wrong answer if missed.

THE PHASES DO NOT SUM TO LCP, AND THE GAP IS NOT SMALL. Each phase is its own
75th percentile, and percentiles are not additive. Measured across twelve real
origins on 2026-08-05, the sum missed the LCP p75 every single time, in both
directions:

    theguardian.com   LCP  920ms   phases   880ms    -40ms
    bbc.co.uk         LCP  917ms   phases  1004ms    +87ms
    gov.uk            LCP  671ms   phases   798ms   +127ms
    nytimes.com       LCP 2292ms   phases  1871ms   -421ms
    stackoverflow.com LCP 1488ms   phases  4104ms  +2616ms

So the phases are reported as shares of THEIR OWN total and never as shares of
LCP, and the two totals are printed side by side rather than reconciled. A tool
that presented "where your 1488ms goes" as a 4104ms breakdown would be talking
nonsense in a confident voice.

THE PHASES DESCRIBE ONLY THE VISITS WHERE THE LCP ELEMENT WAS AN IMAGE. Every
sub-part metric is named `largest_contentful_paint_image_*`, and CrUX publishes
them whatever the split. On gov.uk the LCP element is text for 98% of visits and
the four phases are still returned, describing the other 2%. Presenting that as
the explanation of gov.uk's LCP would describe one visit in fifty and imply it
was all of them.

That also explains most of the Stack Overflow gap above. 12.6% of its visits
have an image LCP, those visits wait 3295ms for the image to start downloading,
and the headline p75 of 1488ms is carried by the fast text majority. The
breakdown is not wrong there, it is answering about a minority — and it is worth
having, because a 3.3 second discovery delay is invisible in the headline
number. It just has to say who it is about.

So the image share travels with the phases everywhere, and leads when it is
small.
"""

# In timeline order, because they are sequential and reading them by size loses
# the thing that makes them legible. Labels match crux.LCP_PHASES.
PHASES = [
    ('server response',
     'before the page had started arriving at all'),
    ('load delay',
     'the page had arrived but the browser had not yet started fetching the '
     'image'),
    ('download',
     'the image itself was on the wire'),
    ('render delay',
     'the image had arrived but was not yet painted'),
]

# Below this the breakdown is describing a minority of visits and has to say so
# before it says anything else. Not a cliff: the exact share is always reported,
# and this only decides what leads.
MINORITY = 0.5


def _rating(p75, histogram):
    """good / needs improvement / poor, read from the histogram's own bins.

    The thresholds are Google's and they are already in the response, as the
    bucket boundaries. Hardcoding 2500 and 4000 here would be a second copy of
    a number Google owns and has moved before.
    """
    if p75 is None or not histogram or len(histogram) < 3:
        return None
    for i, bucket in enumerate(histogram[:3]):
        end = bucket.get('end')
        if end is None or p75 < end:
            return ('good', 'needs improvement', 'poor')[i]
    return 'poor'


def explain(rec):
    """Turn a crux.record() into the LCP breakdown, or say why there is none.

    Takes a record rather than fetching one, so the same analysis serves the
    tool, the text report and the HTML page without three calls or three
    slightly different readings of the same figures.
    """
    metrics = rec.get('metrics') or {}
    lcp = metrics.get('LCP') or {}
    phases = rec.get('lcp_phases') or {}
    shares = rec.get('shares') or {}
    element = shares.get('LCP element') or {}
    image_share = element.get('image')

    out = {
        'scope': rec.get('scope'),
        'period': rec.get('period'),
        'lcp': {'p75': lcp.get('p75'),
                'rating': _rating(lcp.get('p75'), lcp.get('histogram')),
                'histogram': lcp.get('histogram') or []},
        'image_share': image_share,
        'text_share': element.get('text'),
        'phases': [],
        'phase_total': None,
        'dominant': None,
        'minority': None,
        'ttfb': {},
        'rtt': (metrics.get('RTT') or {}).get('p75'),
    }

    # Two time-to-first-bytes, and they are not the same measurement. One is
    # every navigation, the other is only those whose LCP was an image. Where
    # they diverge, the image visits are being served differently from the rest,
    # which is a fact about the site rather than about the page.
    overall_ttfb = (metrics.get('TTFB') or {}).get('p75')
    if overall_ttfb is not None:
        out['ttfb']['all_navigations'] = overall_ttfb
    if 'server response' in phases:
        out['ttfb']['image_lcp_navigations'] = phases['server response']

    if not phases:
        out['available'] = False
        # Not the same as having no field data at all, and the difference
        # matters: the site has real users, Google just has no image-LCP
        # navigations to decompose.
        out['reason'] = ('no_phases' if lcp else 'no_lcp')
        return out

    out['available'] = True
    total = sum(v for v in phases.values() if isinstance(v, (int, float)))
    out['phase_total'] = total
    for label, what in PHASES:
        ms = phases.get(label)
        if ms is None:
            continue
        out['phases'].append({
            'label': label,
            'ms': ms,
            # Of the phase total, NEVER of the LCP p75. See the module docstring.
            'share': (ms / total) if total else None,
            'description': what,
        })
    if out['phases']:
        out['dominant'] = max(out['phases'], key=lambda p: p['ms'])['label']
    if image_share is not None:
        out['minority'] = image_share < MINORITY
    # Reported, never reconciled. The two totals answer different questions over
    # different populations, so a "discrepancy" is the expected state.
    out['gap'] = None if out['lcp']['p75'] is None else total - out['lcp']['p75']
    return out
