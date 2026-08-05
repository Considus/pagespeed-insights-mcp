"""Did the change actually help.

This closes the loop the findings open. Without it the tool tells someone what
to fix and then abandons them at the only question they actually care about,
and they answer it by running the check twice and reading two medians, which is
the thing this whole package exists to stop.

THE RULE: A CHANGE SMALLER THAN THE NOISE IS NOT A CHANGE. Two measurements each
carry the min-max spread across their distinct analyses. If those ranges
overlap, the difference between the medians is inside the instrument's own
wobble and no verdict is available. Only when the ranges are completely disjoint
is a direction reported.

Measured on one unchanged page: performance ran 27 to 37 across three analyses
and Total Blocking Time ran 824ms to 3.05s. A tool comparing medians alone would
have called an unchanged page improved or regressed depending on which afternoon
it ran, and it would have been just as confident either way.

WHAT THIS RULE COSTS, said plainly because it is a real cost. It is
conservative, and it will fail to detect improvements that are genuine but small
relative to the noise. That is deliberate: reporting "no measurable change" when
something did improve wastes a little of someone's confidence, while reporting an
improvement that was noise sends them to tell their client a lie. Those two
errors are not equally bad, so the rule is not balanced between them.

TWO NUMBERS, NOT ONE, WHEN A CHANGE IS REAL. The medians differing by 9.5 points
is the headline, but the ranges only guarantee the smaller gap between their
nearest edges. Both are reported. The guaranteed one is what to quote to someone
who will hold you to it.

WHAT THIS CANNOT SEE. Field data is a 28-day rolling window, so it cannot
reflect a change made this week and comparing it before and after a fix is
meaningless until a month has passed. Lighthouse's own version can change under
you, moving a score without the page moving, so the versions are compared and a
difference is reported as a confound rather than absorbed silently.
"""

# Higher is better for a score out of 100. Lower is better for every metric here
# — they are all durations, except CLS, which is a ratio where less is also
# better. There is no metric in this package where up is good.
SCORES_RISE = True


def _overlap(a, b):
    return a['min'] <= b['max'] and b['min'] <= a['max']


def verdict(before, after, before_n, after_n, higher_is_better):
    """Whether a difference between two spreads is a change at all.

    `before` and `after` are {median, min, max}. The counts matter: one analysis
    has no spread, so min equals max, and two such points differing would look
    disjoint and be reported as a real change on the strength of two anecdotes.
    """
    out = {'before': before, 'after': after,
           'median_change': after['median'] - before['median']}

    if before_n < 2 or after_n < 2:
        out['verdict'] = 'unknown'
        out['why'] = ('one of these is a single analysis, which has no spread, '
                      'so there is nothing to tell a real change from noise')
        return out

    if _overlap(before, after):
        out['verdict'] = 'no measurable change'
        out['why'] = ('the two ranges overlap, so the difference is inside the '
                      'run-to-run noise')
        return out

    # Disjoint. The direction is certain; the size is at least the gap between
    # the nearest edges and at most the gap between the far ones.
    rose = after['min'] > before['max']
    gap = (after['min'] - before['max']) if rose else (before['min'] - after['max'])
    out['at_least'] = gap
    out['verdict'] = 'better' if rose == higher_is_better else 'worse'
    out['why'] = 'the ranges do not overlap'
    return out


def results(before, after):
    """Compare two measure() payloads for the same URL and strategy."""
    out = {'url': after.get('url'), 'strategy': after.get('strategy'),
           'scores': {}, 'metrics': {}, 'notes': [],
           'before_analyses': before.get('analyses'),
           'after_analyses': after.get('analyses')}

    for name, spread in (after.get('scores') or {}).items():
        was = (before.get('scores') or {}).get(name)
        if was:
            out['scores'][name] = verdict(was, spread, before.get('analyses', 0),
                                          after.get('analyses', 0), SCORES_RISE)
    for label, spread in (after.get('metrics') or {}).items():
        was = (before.get('metrics') or {}).get(label)
        if was:
            out['metrics'][label] = verdict(was, spread, before.get('analyses', 0),
                                            after.get('analyses', 0),
                                            higher_is_better=False)

    for side, res in (('before', before), ('after', after)):
        if (res.get('analyses') or 0) < 2:
            out['notes'].append(
                f'The {side} measurement is a single analysis, so it has no '
                'spread and nothing here can be called a change.')

    # A Lighthouse version change moves scores without the page moving. It is
    # the one confound that looks exactly like a result.
    old, new = before.get('lighthouse_version'), after.get('lighthouse_version')
    if old and new and old != new:
        out['notes'].append(
            f'Lighthouse changed from {old} to {new} between these two '
            'measurements. It reweights and revises audits between versions, so '
            'some of any difference below belongs to the tool rather than to '
            'the page.')

    out['findings'] = findings(before.get('findings') or [],
                               after.get('findings') or [])
    return out


def findings(before, after):
    """What stopped failing, what started, and what is still there.

    NOT "fixed" and "broken". A finding is reported only when it fails in every
    distinct analysis, so one dropping off the list means it now passes at least
    once, which is weaker than fixed and is worth the extra words. The same
    threshold makes the other direction sturdy: something newly listed failed
    every time it was looked at.
    """
    was = {f['id']: f for f in before}
    now = {f['id']: f for f in after}
    title = lambda f: f.get('title', f['id'])
    return {
        'gone': [{'id': i, 'title': title(was[i])} for i in was if i not in now],
        'new': [{'id': i, 'title': title(now[i]), 'impact': now[i].get('impact'),
                 'category': now[i].get('category')} for i in now if i not in was],
        'remaining': [{'id': i, 'title': title(now[i]), 'impact': now[i].get('impact'),
                       'category': now[i].get('category')}
                      for i in now if i in was],
    }


def snapshot(result):
    """The part of a measurement worth keeping as a baseline.

    Small on purpose. A full result carries Google's remediation prose and the
    offending elements for every finding, which is most of its size and none of
    it needed to answer whether something moved. Titles are kept so a comparison
    can name what changed without refetching.
    """
    return {
        'url': result.get('url'),
        'strategy': result.get('strategy'),
        'recorded': result.get('recorded'),
        'analyses': result.get('analyses'),
        'scores': result.get('scores') or {},
        'metrics': result.get('metrics') or {},
        'lighthouse_version': result.get('lighthouse_version'),
        'findings': [{'id': f['id'], 'title': f.get('title'),
                      'category': f.get('category'), 'impact': f.get('impact')}
                     for f in (result.get('findings') or [])],
    }
