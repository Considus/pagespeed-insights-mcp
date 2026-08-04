"""What is wrong with a page, ranked by what fixing it is worth.

PageSpeed returns 153 audits per call and this package used 5 of them. The rest
name every failure it found, with Google's own remediation text, the offending
elements, and an estimate of what each fix would return. That is the material
for answering "what is wrong and what should I do first", and it costs nothing
extra because it arrives in responses already fetched.

Three rules make it honest rather than a data dump, and all three come from
measuring rather than assuming.

ONLY REPORT WHAT REPRODUCES. Audits are as noisy as the scores they explain.
Measured across three distinct analyses of one unchanged page on 2026-08-04,
`unused-javascript` claimed savings of 1200ms, 1800ms and 1200ms, a 1.5x swing.
An audit that fails in one analysis and passes in another is not a finding, it
is the instrument moving. So a finding must fail in EVERY distinct analysis
collected before it is reported at all.

SAVINGS GET A MEDIAN AND A SPREAD, like every other number here. Quoting one
run's estimate would be the exact fault this package exists to correct, and it
would be worse than quoting a single score, because a number attached to an
instruction invites someone to go and do work.

RANK BY WHAT MOVES THE SCORE, NOT BY THE BIGGEST NUMBER. The performance score
is five metrics and nothing else: on Lighthouse 13, Total Blocking Time weighs
30, LCP 25, CLS 25, First Contentful Paint 10 and Speed Index 10. Every other
performance audit weighs ZERO and exists to explain those five. Twelve of the
eighteen failures on one real page contributed nothing directly. Ranking by raw
estimated savings would therefore send someone to fix things that cannot move
the number, which is worse than saying nothing.

The weights are read from the response rather than written down here, because
they are Lighthouse's to change and have changed before.

WHAT THIS CANNOT DO, stated because the output implies otherwise if it is not.
The estimates do not compose: two fixes claiming 6750ms and 1200ms off LCP do
not return 7950ms, they overlap, and Lighthouse models no interaction between
them. They rank well and they add up badly. Several of the largest findings are
also not on the page at all, being hosting, DNS or third-party decisions. This
produces a prioritised list, not a route to 100.
"""
import statistics

# Audits whose failure is a fact about the network or the business rather than
# the page, so they are reported but never ranked first. Fixing them is real
# work by someone who may not be the person reading this.
OFF_PAGE = {
    'redirects', 'server-response-time', 'uses-text-compression',
    'third-party-cookies', 'third-party-summary', 'uses-http2',
}


def weights_of(lhr):
    """Metric id -> weight, read from the response, per category.

    Not hardcoded. Lighthouse has changed these between versions, and a stale
    table here would silently rank by last year's priorities.
    """
    out = {}
    for name, cat in (lhr.get('categories') or {}).items():
        for ref in cat.get('auditRefs') or []:
            if ref.get('weight'):
                out[ref['id']] = ref['weight']
    return out


def category_of(lhr):
    """Audit id -> (category, group). Used to say where a finding belongs."""
    out = {}
    for name, cat in (lhr.get('categories') or {}).items():
        for ref in cat.get('auditRefs') or []:
            out.setdefault(ref['id'], (name, ref.get('group')))
    return out


def _failing(audit):
    """A real failure, not a pass, a nudge, or something inapplicable.

    `notApplicable` and `manual` are excluded: the first does not apply to this
    page and the second is a question Lighthouse cannot answer. `informative`
    has no score at all, so there is nothing to fail.
    """
    if audit.get('scoreDisplayMode') in ('notApplicable', 'manual', 'informative'):
        return False
    score = audit.get('score')
    return score is not None and score < 0.9


def record(lhr):
    """One analysis reduced to what a finding needs.

    Deliberately compact. Retaining whole audits across five analyses is about
    2.4MB, almost all of it detail rows that are identical between runs, so the
    varying parts are kept per analysis and the rest taken once at the end.
    """
    out = {}
    for aid, audit in (lhr.get('audits') or {}).items():
        if not _failing(audit):
            continue
        details = audit.get('details') or {}
        out[aid] = {
            'score': audit.get('score'),
            'savings': {k: v for k, v in (audit.get('metricSavings') or {}).items()
                        if isinstance(v, (int, float)) and v > 0},
            'ms': details.get('overallSavingsMs'),
            'bytes': details.get('overallSavingsBytes'),
            'items': len(details.get('items') or []),
        }
    return out


def _spread(values):
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values)}


def collect(records, lhr, limit=None):
    """Findings that failed in EVERY analysis, ranked by weighted impact.

    `records` is one dict per distinct analysis from record(). `lhr` is any one
    full response, used for the static parts: titles, Google's descriptions,
    the offending elements, and the weights.
    """
    if not records:
        return []
    always = set(records[0])
    for r in records[1:]:
        always &= set(r)

    weights = weights_of(lhr)
    where = category_of(lhr)
    audits = lhr.get('audits') or {}
    findings = []

    for aid in always:
        audit = audits.get(aid) or {}
        category, group = where.get(aid, ('other', None))
        # The five metric audits ARE the score. They are reported by
        # check_pagespeed with their own median and spread, and listing
        # "First Contentful Paint" as a thing to fix says nothing about how.
        if group == 'metrics':
            continue
        per_metric = {}
        for metric in {m for r in records for m in r[aid]['savings']}:
            vals = [r[aid]['savings'][metric] for r in records if metric in r[aid]['savings']]
            # Only if EVERY analysis agreed this fix helps that metric. One
            # analysis claiming a saving the others do not see is noise.
            if len(vals) == len(records):
                per_metric[metric] = _spread(vals)

        scores = [r[aid]['score'] for r in records if r[aid]['score'] is not None]
        median_score = statistics.median(scores) if scores else 0

        # Two currencies, because the categories work differently and pretending
        # otherwise ranks nonsense.
        #
        # Performance: the score is five metrics, and a diagnostic moves it only
        # through the milliseconds it claims off one of them. So impact is the
        # median saving weighted by what that metric is worth. An audit with no
        # metricSavings scores zero and sinks, correctly, because it explains a
        # metric rather than moving it.
        #
        # Everywhere else: each audit carries its own weight and failing it
        # costs a share of the category score directly. Impact is the points
        # lost, which is what fixing it would return.
        if category == 'performance':
            impact = sum(v['median'] * weights.get(_METRIC_AUDIT.get(m, ''), 0)
                         for m, v in per_metric.items())
        else:
            impact = weights.get(aid, 0) * (1 - median_score)
        findings.append({
            'id': aid,
            'title': audit.get('title', aid),
            'description': (audit.get('description') or '').split('[Learn')[0].strip(),
            'category': category,
            'group': group,
            'score': _spread(scores) if scores else None,
            'unit': 'weighted-ms' if category == 'performance' else 'score-points',
            'savings': per_metric,
            'impact': round(impact),
            'items': max(r[aid]['items'] for r in records),
            'off_page': aid in OFF_PAGE,
            'weighted': bool(weights.get(aid)),
        })

    # Sorted within category, never across it. Weighted milliseconds and score
    # points are different units, and one list ordered by both would be sorted
    # by nothing.
    findings.sort(key=lambda f: (f['category'], -f['impact'], f['off_page'], f['id']))
    if limit:
        kept, seen_cat = [], {}
        for f in findings:
            n = seen_cat.get(f['category'], 0)
            if n < limit:
                kept.append(f); seen_cat[f['category']] = n + 1
        return kept
    return findings


# metricSavings names the metric; the weight is held against the audit id that
# measures it. This is the join between the two.
_METRIC_AUDIT = {
    'LCP': 'largest-contentful-paint',
    'FCP': 'first-contentful-paint',
    'TBT': 'total-blocking-time',
    'CLS': 'cumulative-layout-shift',
    'SI': 'speed-index',
    'INP': 'interaction-to-next-paint',
}
