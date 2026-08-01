"""Turning results into text.

There is one formatter and both faces use it. The CLI prints what this returns
and the MCP server puts the same string in its tool result, so a person reading
the terminal and an assistant reading the tool output are looking at identical
words. Two formatters would drift, and the first thing they would disagree about
is what a number means — which is the failure this whole package exists to
prevent, arriving through the back door.
"""

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
            cost += (f", {res['cached_replays']} of them a cached analysis "
                     'replayed on an')
            lines.append(cost)
            lines.append('    identical timestamp or identical measurements')
        else:
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
        whose = ('this URL' if res['field_scope'] == 'url'
                 else 'the whole origin (Google had none for this URL alone)')
        lines.append(f'  Real users, 28-day p75 — {whose}')
        for label, value in res['field'].items():
            lines.append(f"    {label:<16} {duration(label, value['p75']):>8}"
                         f"   {value.get('category', '')}")
    return '\n'.join(lines)


def crux_record(rec, url):
    lines = [f"{url}  [real users, {rec['scope']}-level, 28-day p75]"]
    period = rec.get('period')
    if period:
        lines.append(f"  collected {period['first']} to {period['last']}")
    if not rec['metrics']:
        return '\n'.join(lines + ['  ' + NO_FIELD_NOTE])
    for label, value in rec['metrics'].items():
        lines.append(f"    {label:<6} {duration(label, value['p75']):>8}")
    return '\n'.join(lines)


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
