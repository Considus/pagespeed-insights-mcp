"""Turning results into text.

There is one formatter and both faces use it. The CLI prints what this returns
and the MCP server puts the same string in its tool result, so a person reading
the terminal and an assistant reading the tool output are looking at identical
words. Two formatters would drift, and the first thing they would disagree about
is what a number means — which is the failure this whole package exists to
prevent, arriving through the back door.
"""
from . import lcp as _lcp

NOISE_NOTE = (
    'Lab scores are simulated on Google\'s hardware. The spread is real '
    'run-to-run noise, so treat a change inside it as no change.')

NO_FIELD_NOTE = (
    'No field data: not enough real Chrome traffic to clear Google\'s '
    'anonymity threshold. Expected for a small or pre-launch site. The lab '
    'scores above are a simulation, not evidence about real visitors.')


def duration(label, value):
    """CLS is a unitless ratio; everything else here is milliseconds."""
    if value is None:
        return '—'
    if label == 'CLS':
        return f'{value:.3f}'
    return f'{value / 1000:.2f} s' if value >= 1000 else f'{value:.0f} ms'


def _spread_note(label, value, numeric=False):
    if value['min'] == value['max']:
        return ''
    if numeric:
        return f"   (ran {value['min']:.0f}-{value['max']:.0f})"
    return f"   (ran {duration(label, value['min'])}-{duration(label, value['max'])})"


def result(res):
    """One URL and strategy: scores, metrics, and PSI's embedded field data."""
    analyses = res['analyses']
    word = 'analysis' if analyses == 1 else 'analyses'
    lines = [f"{res['url']}  [{res['strategy']}, median of {analyses} distinct {word}]"]

    # Two entry points reach this. measure() collects until it has N distinct
    # analyses and knows what that cost in calls and seconds. summarise() on its
    # own only knows how many of the runs it was handed were replays. Both have
    # to say that replays happened and why, or the reader is left thinking a
    # median of five was five measurements.
    if res.get('calls'):
        plural = '' if res['calls'] == 1 else 's'
        cost = f"  {res['calls']} call{plural} over {res['elapsed']:.0f}s"
        if res.get('cached_replays'):
            # NOT "dropped". Every analysis above is genuine; these are calls
            # that came back with one already seen. They are the waiting, not
            # results thrown away, and the earlier wording read as the latter.
            cost += (f", {res['cached_replays']} of which returned an analysis "
                     'already seen')
        lines.append(cost)
    elif res.get('cached_replays'):
        lines += [
            f"  NOTE: {res['cached_replays']} of {res['requested']} runs were PSI replaying a",
            '    cached analysis and were dropped, on an identical timestamp or',
            '    identical measurements. Just after a deploy a cached result predates',
            '    the change, so re-run in a few minutes.']

    if res.get('short'):
        lines += [
            f"  NOTE: asked for {res['requested']} distinct analyses and got "
            f"{analyses} before the",
            '    time budget ran out. Google re-analyses a URL about once a minute,',
            '    so more takes longer rather than more requests. The spread below is',
            f'    across {analyses}, which is the number to judge it on.']
    elif analyses == 1:
        lines += [
            '  NOTE: this is one analysis, so there is no spread and no way to tell',
            '    a real change from run-to-run noise. Treat it as an anecdote.']

    lines.append('  Lighthouse (lab)')
    for cat, value in res['scores'].items():
        lines.append(f"    {cat:<16} {value['median']:5.0f}"
                     f"{_spread_note(cat, value, numeric=True)}")
    for label in ('LCP', 'CLS', 'TBT', 'FCP', 'Speed Index'):
        value = res['metrics'].get(label)
        if value:
            lines.append(f"    {label:<16} {duration(label, value['median']):>8}"
                         f"{_spread_note(label, value)}")

    if res['field']:
        # PSI follows redirects and reports field data for wherever it landed,
        # with nothing in the response marking the substitution. Asking about
        # bbc.co.uk returns bbc.com's numbers, and those two disagree on CLS by
        # two categories. Saying "this URL" there would be false.
        subject = res.get('field_subject')
        moved = subject and subject.rstrip('/') != res['url'].rstrip('/')
        if res['field_scope'] == 'url':
            # Name the page, always. A run with --field prints this block and
            # then the Chrome UX Report block, and those legitimately differ:
            # one is this page, the other is every page on the site. Headed
            # "real users" twice with different numbers, they read as a
            # contradiction. Saying which is which, in the same shape both
            # times, is what makes them legible as two questions.
            whose = f'this page only, {subject or res["url"]}'
            if moved:
                # Parenthesised, not another dash. The heading already has one
                # and two in a line is a sentence nobody parses on first read.
                whose += ' (where this URL redirects to)'
        else:
            whose = ('every page on the site, because Google had nothing for '
                     'this page alone')
        lines.append(f'  Real users, 28-day p75 — {whose}')
        for label, value in res['field'].items():
            lines.append(f"    {label:<16} {duration(label, value['p75']):>8}"
                         f"   {value.get('category', '')}")
    return '\n'.join(lines)


def crux_record(rec, url):
    # Same shape of sentence as the embedded block in result(), on purpose.
    # This one is almost always the whole site while that one is a single page,
    # so the numbers differ for a good reason. Two headings reading "real users"
    # with different figures under them is what makes that look like an error.
    whose = ('every page on the site' if rec['scope'] == 'origin'
             else 'this page only')
    lines = [f'{url}  [real users, 28-day p75 — {whose}]']
    period = rec.get('period')
    if period:
        lines.append(f"  collected {period['first']} to {period['last']}")
    if not rec['metrics']:
        return '\n'.join(lines + ['  ' + NO_FIELD_NOTE])
    for label, value in rec['metrics'].items():
        bar = _histogram(value.get('histogram'))
        lines.append(f"    {label:<6} {duration(label, value['p75']):>8}   {bar}")

    # The four phases only exist when the LCP element is an image, and they are
    # the difference between "the server is slow" and "the image starts late".
    # Rendered through the same analysis the explain_lcp tool uses, so the short
    # version here cannot quietly drop the caveats the long version carries.
    analysis = _lcp.explain(rec)
    if analysis['available']:
        lines.append('  Where the LCP time goes')
        lines += ['  ' + line for line in _phase_lines(analysis)]

    shares = rec.get('shares') or {}
    for label, fractions in shares.items():
        top = ', '.join(f'{k} {v:.0%}' for k, v in
                        sorted(fractions.items(), key=lambda kv: -kv[1])[:3] if v >= 0.01)
        lines.append(f'  {label}: {top}')
    return '\n'.join(lines)


def _coverage_line(analysis):
    """Who the breakdown is actually about.

    Always stated, and stated first when it is a minority, because the four
    phases are collected only from visits whose LCP element was an image. On
    gov.uk that is 2% of visits, and a breakdown headed "where the LCP time
    goes" with no qualifier there describes one visit in fifty as though it
    were all of them.
    """
    share = analysis.get('image_share')
    if share is None:
        return ('These four phases cover only the visits where the largest '
                'element was an image. Google did not say what share of visits '
                'that is here.')
    text = analysis.get('text_share') or (1 - share)
    if analysis.get('minority'):
        return (f'MOST VISITS ARE NOT DESCRIBED BELOW. The largest element is '
                f'an image for {share:.0%} of visits and text for {text:.0%}, '
                f'and Google decomposes only the image ones. This is that '
                f'{share:.0%}.')
    return (f'The largest element is an image for {share:.0%} of visits, which '
            f'is what these four phases are measured over.')


def _phase_lines(analysis):
    """The four phases with the two things that make them readable honestly."""
    lines = ['  ' + _coverage_line(analysis), '']
    for phase in analysis['phases']:
        share = f"{phase['share']:.0%}" if phase['share'] is not None else ''
        lines.append(f"  {phase['label']:<16} {duration('x', phase['ms']):>8} "
                     f"{share:>5}   {phase['description']}")
    if analysis.get('dominant'):
        lines += ['', f"  Longest phase: {analysis['dominant']}."]

    total, p75 = analysis['phase_total'], analysis['lcp']['p75']
    if total is not None and p75 is not None:
        # Printed side by side and left unreconciled on purpose. They answer
        # different questions over different populations, so a gap is the
        # expected state rather than a discrepancy to explain away.
        lines += [
            '',
            f"  Those four total {duration('x', total)} against an LCP of "
            f"{duration('LCP', p75)}. They do not add up to it and are not",
            '    meant to: each is its own 75th percentile, taken over the image '
            'visits only, and',
            '    percentiles do not add. The shares above are of the '
            f"{duration('x', total)}, not of the LCP."]
    return lines


def lcp(analysis, url):
    """The LCP breakdown for one URL."""
    whose = ('every page on the site' if analysis.get('scope') == 'origin'
             else 'this page only')
    lines = [f'{url}  [where the LCP time goes, real users — {whose}]']
    period = analysis.get('period')
    if period:
        lines.append(f"  collected {period['first']} to {period['last']}")

    p75 = analysis['lcp']['p75']
    if p75 is not None:
        rating = analysis['lcp'].get('rating')
        lines.append(f"  LCP {duration('LCP', p75)}"
                     + (f'   {rating}' if rating else ''))

    if not analysis['available']:
        lines.append('')
        if analysis.get('reason') == 'no_lcp':
            lines.append('  ' + NO_FIELD_NOTE)
        else:
            lines += [
                '  No breakdown available. Google decomposes the LCP only for '
                'visits where the',
                '    largest element was an image, and it published none for '
                'this site. The largest',
                '    element is text for essentially every visit, and text has '
                'no download phase to',
                '    measure. That is a normal result and not a fault.']
        return '\n'.join(lines)

    lines.append('')
    lines += _phase_lines(analysis)

    ttfb = analysis.get('ttfb') or {}
    both = ttfb.get('all_navigations'), ttfb.get('image_lcp_navigations')
    if all(v is not None for v in both):
        # Two different populations again, and where they diverge the image
        # visits are being served differently from the rest.
        lines += ['', f"  Server response {duration('x', both[0])} across all "
                      f"visits, {duration('x', both[1])} on the image ones."]
    elif both[0] is not None:
        lines += ['', f"  Server response {duration('x', both[0])}."]
    if analysis.get('rtt') is not None:
        lines.append(f"  Network round trip {duration('x', analysis['rtt'])}, "
                     'which is where the audience sits rather than how fast '
                     'the server is.')
    return '\n'.join(lines)


LCP_NOTE = (
    'The four phases are shares of their own total, not of the LCP, and they '
    'are measured only over visits whose largest element was an image. This is '
    'field data from real Chrome users, so there is no run-to-run noise here, '
    'but it is also 28 days old at the edges and will not show a change made '
    'this week.')


def _histogram(buckets):
    """The good / needs-improvement / poor split, as words.

    A p75 hides its own tail. On one real origin the p75 was a comfortable
    1.19s while 1.7% of visits took over four seconds, and those two facts lead
    to different work.
    """
    if not buckets or len(buckets) < 3:
        return ''
    good, ok, poor = (b.get('density') or 0 for b in buckets[:3])
    return f'{good:.0%} good, {ok:.0%} fair, {poor:.0%} poor'


def crux_history(hist, url):
    """The series, plus where it started and finished.

    A change is only reported as a direction, never as a verdict. CrUX has no
    error bars to compare against, so calling a drift 'a regression' would be
    the same overclaiming this package refuses to do with lab scores.
    """
    periods = hist.get('periods') or []
    lines = [f"{url}  [real users, {hist['scope']}-level, {len(periods)} weekly periods]"]
    if periods:
        lines.append(f"  {periods[0]['first']} to {periods[-1]['last']}")

    for label, series in hist['metrics'].items():
        known = [v for v in series if v is not None]
        if not known:
            continue
        first, last = known[0], known[-1]
        change = last - first
        if label == 'CLS':
            direction = f'{change:+.3f}'
        else:
            direction = f'{change:+.0f} ms'
        gaps = sum(1 for v in series if v is None)
        gap_note = f'  [{gaps} period(s) with too little data]' if gaps else ''
        lines.append(f"    {label:<6} {duration(label, first):>8} -> "
                     f"{duration(label, last):>8}   {direction}{gap_note}")
    return '\n'.join(lines)


# Findings are ranked within their category and never across it, because the two
# currencies are not comparable. A performance diagnostic is worth the
# milliseconds it claims off a weighted metric; an accessibility failure is
# worth the points it costs that category directly.
CATEGORY_TITLE = {
    'performance': 'Performance',
    'accessibility': 'Accessibility',
    'best-practices': 'Best practices',
    'seo': 'SEO',
}

FINDINGS_NOTE = (
    'Savings are Google\'s estimates for each fix on its own. They do not add '
    'up: two fixes claiming time off the same metric overlap, and Lighthouse '
    'models no interaction between them. Treat the order as the useful part.')


def findings(items, url, limit=5):
    """Findings grouped by category, ranked within each, with their spread."""
    if not items:
        return f'{url}\n  Nothing failed consistently enough to report.'

    groups = {}
    for f in items:
        groups.setdefault(f['category'], []).append(f)

    lines = [f'{url}  [what is failing, ranked by what fixing it is worth]']
    for cat in ('performance', 'accessibility', 'best-practices', 'seo'):
        found = groups.get(cat)
        if not found:
            continue
        shown = found[:limit]
        more = len(found) - len(shown)
        lines.append(f'\n  {CATEGORY_TITLE.get(cat, cat)}'
                     + (f'  ({len(found)} findings)' if len(found) > 1 else ''))
        for f in shown:
            worth = _worth(f)
            lines.append(f"    {f['title']}{'  [not on the page]' if f['off_page'] else ''}")
            if worth:
                lines.append(f'      {worth}')
            if f['items']:
                lines.append(f"      {f['items']} affected element(s)")
            if f['description']:
                lines.append(f"      {f['description'][:150]}")
        if more:
            lines.append(f'    ... and {more} more, lower impact')
    return '\n'.join(lines)


def _worth(f):
    """What the fix is worth, in the currency of its category."""
    if f['unit'] == 'weighted-ms' and f['savings']:
        parts = []
        for metric, v in sorted(f['savings'].items(), key=lambda kv: -kv[1]['median']):
            spread = ('' if v['min'] == v['max']
                      else f" (ran {v['min']:.0f}-{v['max']:.0f})")
            parts.append(f"{metric} {v['median']:.0f} ms{spread}")
        return 'saves ' + ', '.join(parts)
    if f['unit'] == 'score-points' and f['impact']:
        return f"worth {f['impact']:.0f} point(s) of the {CATEGORY_TITLE.get(f['category'], '')} score"
    return ''
