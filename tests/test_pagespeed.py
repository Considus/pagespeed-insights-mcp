"""Offline tests. Nothing here touches the network or a real API key.

The two regression tests at the top are the point of this file. Both bugs were
found by running against the live API and both were silent — they produced a
plausible number rather than an error, which is the only kind of bug that
matters in a measurement tool.
"""
import contextlib
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import setup                                                      # noqa: E402
from pagespeed_insights import (compare, config, crux, findings,  # noqa: E402
                                lcp, psi, render, report)
from pagespeed_insights.errors import CruxUnavailable             # noqa: E402


# ---------------------------------------------------------------------------
# Setup page helpers
#
# The page is assembled per platform and most of it is unreachable from the
# machine running the tests, so the only way to cover it is to say which
# platform to be. CI runs this on all three, which means each platform verifies
# its own blocks for real and the other two by simulation.
# ---------------------------------------------------------------------------
_FAKE = {
    'darwin': ('posix', 'darwin', '/home/x/pagespeed-insights-mcp', '/usr/bin/python3'),
    'linux': ('posix', 'linux', '/home/x/pagespeed-insights-mcp', '/usr/bin/python3'),
    # Program Files on purpose. The space is what broke the unquoted form, and a
    # fixture without one would pass whether or not the quoting came back.
    'nt': ('nt', 'win32', r'C:\Users\x\pagespeed-insights-mcp',
           r'C:\Program Files\Python312\python.exe'),
}


@contextlib.contextmanager
def as_platform(name):
    saved = (os.name, sys.platform, setup.HERE, sys.executable)
    os.name, sys.platform, setup.HERE, sys.executable = _FAKE[name]
    try:
        yield
    finally:
        os.name, sys.platform, setup.HERE, sys.executable = saved


def headings(fragment):
    return re.findall(r'<h3>(.*?)</h3>', fragment)


def commands(fragment):
    """The text of each block, as the reader would copy it."""
    return [html.unescape(b).strip()
            for b in re.findall(r'<pre class="term">(.*?)</pre>', fragment, re.S)]


def visible(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', fragment)))


class ClsScaling(unittest.TestCase):
    """PSI reports its embedded CLS multiplied by 100. Its own histogram proves
    it: the bins are 0-10 / 10-25 / 25+, which are the 0.1 and 0.25 thresholds
    scaled, while the LCP bins beside them are plain milliseconds.

    Unscaled, a healthy 0.08 is reported as 8.0 — a catastrophic score — on the
    one metric where the difference between those two numbers is the whole
    story. It reads as a real result, so nothing catches it but a test.
    """

    def _summary(self, percentile):
        return psi.summarise(
            [{'scores': {}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://example.com/', 'mobile', 'url',
            {'CUMULATIVE_LAYOUT_SHIFT_SCORE':
                {'percentile': percentile, 'category': 'FAST'}})

    def test_cls_is_divided_by_one_hundred(self):
        self.assertAlmostEqual(self._summary(8)['field']['CLS']['p75'], 0.08)

    def test_a_bad_cls_is_still_bad(self):
        self.assertAlmostEqual(self._summary(30)['field']['CLS']['p75'], 0.30)

    def test_lcp_is_not_scaled(self):
        result = psi.summarise(
            [{'scores': {}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://example.com/', 'mobile', 'url',
            {'LARGEST_CONTENTFUL_PAINT_MS': {'percentile': 1195, 'category': 'FAST'}})
        self.assertEqual(result['field']['LCP']['p75'], 1195)


class CruxNumericTypes(unittest.TestCase):
    """CrUX returns CLS as a string while every other metric is a number, so
    `cls > 0.1` raises TypeError on Python 3 — and only for the metric where a
    small value matters most."""

    def test_string_cls_becomes_float(self):
        self.assertEqual(crux._number('0.08'), 0.08)
        self.assertIsInstance(crux._number('0.00'), float)

    def test_numbers_pass_through(self):
        self.assertEqual(crux._number(926), 926)

    def test_nonsense_is_left_alone_rather_than_crashing(self):
        self.assertEqual(crux._number('n/a'), 'n/a')


class Deduplication(unittest.TestCase):
    """PSI caches a result per URL and replays it. Five 'runs' can be one
    analysis five times, which turns a median into a vote for whatever Google
    had cached — including, just after a deploy, the pre-deploy score."""

    @staticmethod
    def _run(stamp, performance):
        return {'scores': {'performance': performance}, 'metrics': {}, 'fetchTime': stamp}

    def test_replays_are_dropped_and_counted(self):
        result = psi.summarise(
            [self._run('t1', 100), self._run('t1', 100), self._run('t2', 60)],
            'https://example.com/', 'mobile')
        self.assertEqual(result['analyses'], 2)
        self.assertEqual(result['requested'], 3)
        self.assertEqual(result['cached_replays'], 1)

    def test_a_replay_cannot_swing_the_median(self):
        """Two cached 100s and one real 60 must not median to 100."""
        result = psi.summarise(
            [self._run('t1', 100), self._run('t1', 100), self._run('t2', 60)],
            'https://example.com/', 'mobile')
        self.assertEqual(result['scores']['performance']['median'], 80)

    def test_runs_without_a_fetchtime_are_kept(self):
        result = psi.summarise(
            [self._run(None, 90), self._run(None, 70)], 'https://example.com/', 'mobile')
        self.assertEqual(result['analyses'], 2)


class ReplayUnderAFreshTimestamp(unittest.TestCase):
    """The fetchTime rule leaks, and this is the leak.

    These are the analyses catchlight.app actually returned on 2026-08-01. Eleven
    runs reported four distinct analyses, and two of the four agreed on FCP to
    thirteen decimal places under different timestamps. Two independent
    Lighthouse runs do not agree to the femtosecond, so it was one analysis
    served twice and counted as corroboration. Silent, like the other two at the
    top of this file: it produced a plausible number rather than an error.
    """

    A = {'fetchTime': '2026-08-01T21:04:10Z', 'scores': {'performance': 98},
         'metrics': {'LCP': 2251, 'FCP': 1686.8756582616209}}
    B = {'fetchTime': '2026-08-01T21:04:22Z', 'scores': {'performance': 98},
         'metrics': {'LCP': 2270, 'FCP': 1717.6918476482374}}
    C = {'fetchTime': '2026-08-01T21:04:41Z', 'scores': {'performance': 98},
         'metrics': {'LCP': 2251, 'FCP': 1686.8756582616209}}

    def test_identical_measurements_are_one_analysis(self):
        result = psi.summarise([self.A, self.B, self.C],
                               'https://catchlight.app/', 'mobile')
        self.assertEqual(result['analyses'], 2)
        self.assertEqual(result['cached_replays'], 1)

    def test_the_sample_cannot_be_inflated_by_a_repeat(self):
        """Three runs that are really two must not read as three, because the
        count is the whole basis for trusting the median."""
        many = psi.summarise([self.A, self.C, dict(self.C), self.B],
                             'https://catchlight.app/', 'mobile')
        self.assertEqual(many['analyses'], 2)

    def test_the_spread_still_comes_from_both_real_analyses(self):
        result = psi.summarise([self.A, self.B, self.C],
                               'https://catchlight.app/', 'mobile')
        self.assertEqual(result['metrics']['LCP']['min'], 2251)
        self.assertEqual(result['metrics']['LCP']['max'], 2270)

    def test_genuinely_different_runs_all_survive(self):
        """The rule has to be wrong in only one direction. Dropping real
        analyses would shrink the sample it exists to protect."""
        result = psi.summarise([self.A, self.B], 'https://catchlight.app/', 'mobile')
        self.assertEqual(result['analyses'], 2)
        self.assertEqual(result['cached_replays'], 0)

    def test_runs_that_measured_nothing_are_not_collapsed(self):
        """Two empty runs are two failures, not a replay. Treating them as one
        would understate what was attempted."""
        empty = {'fetchTime': None, 'scores': {}, 'metrics': {}}
        result = psi.summarise([empty, dict(empty)], 'https://example.com/', 'mobile')
        self.assertEqual(result['analyses'], 2)

    def test_the_note_no_longer_claims_fetchtime_is_the_only_rule(self):
        result = psi.summarise([self.A, self.B, self.C],
                               'https://catchlight.app/', 'mobile')
        note = render.result(result)
        self.assertIn('identical measurements', note)
        self.assertNotIn('identical fetchTime', note)


class CollectUntilDistinct(unittest.TestCase):
    """`runs` is a target for DISTINCT analyses, not a number of requests.

    Google re-analyses a URL about once a minute and replays the cached result
    to everything that asks in between. Measured 2026-08-01: 60 calls at 5s
    intervals produced 9 distinct analyses. So calling five times in a row is
    one measurement repeated five times, and a median over it is a median of
    one wearing a disguise. These pin the collecting behaviour that replaced it.
    """

    @staticmethod
    def _payload(fcp, stamp):
        return {'lighthouseResult': {
            'fetchTime': stamp,
            'categories': {'performance': {'score': 0.98}},
            'audits': {'first-contentful-paint': {'numericValue': fcp}}}}

    def _run(self, script, **kw):
        """Drive measure() off a fixed sequence of API responses."""
        state = {'i': 0}

        def fake_fetch(url, strategy, key, **_):
            payload = script[min(state['i'], len(script) - 1)]
            state['i'] += 1
            return payload

        real, psi.fetch = psi.fetch, fake_fetch
        self.slept = []
        try:
            return psi.measure('https://x.test/', runs=kw.pop('runs', 3), key='k',
                               sleep=self.slept.append, **kw)
        finally:
            psi.fetch = real

    def test_replays_do_not_count_towards_the_target(self):
        result = self._run([self._payload(1000, 't1'), self._payload(1000, 't1'),
                            self._payload(1100, 't2'), self._payload(1200, 't3')])
        self.assertEqual(result['analyses'], 3)
        self.assertEqual(result['calls'], 4)
        self.assertEqual(result['cached_replays'], 1)
        self.assertFalse(result['short'])

    def test_a_replay_under_a_fresh_timestamp_does_not_count_either(self):
        """The two-backend case. Same measurements, different stamp."""
        result = self._run([self._payload(1000, 't1'), self._payload(1000, 't2'),
                            self._payload(1100, 't3'), self._payload(1200, 't4')])
        self.assertEqual(result['analyses'], 3)
        self.assertEqual(result['cached_replays'], 1)

    def test_it_gives_up_on_budget_and_says_so_rather_than_pretending(self):
        """Reporting two analyses is honest. Reporting five would not be."""
        result = self._run([self._payload(1000, 't1')], budget=0)
        self.assertTrue(result['short'])
        self.assertEqual(result['analyses'], 1)
        self.assertEqual(result['requested'], 3)

    def test_the_target_is_still_reported_when_it_falls_short(self):
        """The spread means nothing without knowing what it is across."""
        result = self._run([self._payload(1000, 't1')], budget=0)
        self.assertIn('asked for 3 distinct', render.result(result))

    def test_no_wait_after_the_last_analysis_lands(self):
        """Sleeping once more would add 15s to every check for nothing."""
        self._run([self._payload(1000, 't1'), self._payload(1100, 't2'),
                   self._payload(1200, 't3')])
        self.assertEqual(len(self.slept), 2)      # 3 calls, 2 gaps

    def test_one_analysis_is_flagged_as_an_anecdote(self):
        result = self._run([self._payload(1000, 't1')], runs=1)
        self.assertFalse(result['short'])
        self.assertIn('anecdote', render.result(result))

    def _run_with_progress(self, script, runs=2):
        seen, state = [], {'i': 0}

        def fake_fetch(url, strategy, key, **_):
            p = script[min(state['i'], len(script) - 1)]
            state['i'] += 1
            return p

        real, psi.fetch = psi.fetch, fake_fetch
        try:
            psi.measure('https://x.test/', runs=runs, key='k', sleep=lambda s: None,
                        progress=lambda d, t, u, s, fresh: seen.append((d, fresh)))
        finally:
            psi.fetch = real
        return seen

    def test_progress_counts_distinct_collected_not_calls_made(self):
        """The count is measurements, so a replay must not advance it."""
        seen = self._run_with_progress(
            [self._payload(1000, 't1'), self._payload(1000, 't1'),
             self._payload(1100, 't2')])
        self.assertEqual([d for d, _ in seen], [1, 1, 2])   # middle one is a replay

    def test_progress_fires_on_every_call_including_the_replays(self):
        """A timeout fix, not cosmetics.

        MCP clients reset their request timeout when a progress notification
        arrives. Google produces a genuinely new analysis about once a minute,
        so a callback firing only on new analyses beats at ~60s intervals,
        which is at or past most clients' default timeout. Measured live on
        2026-08-05: a 2-run report emitted its two notifications 58 seconds
        apart, and every run through a client reported a timeout. Polling is
        every 15s, so firing per call gives four beats inside that window.
        """
        seen = self._run_with_progress(
            [self._payload(1000, 't1'), self._payload(1000, 't1'),
             self._payload(1100, 't2')])
        self.assertEqual(len(seen), 3, 'a replay produced no heartbeat')
        self.assertEqual([fresh for _, fresh in seen], [True, False, True])


class MedianAndSpread(unittest.TestCase):
    def test_spread_travels_with_the_median(self):
        runs = [{'scores': {'performance': s}, 'metrics': {}, 'fetchTime': f't{i}'}
                for i, s in enumerate((80, 90, 100))]
        score = psi.summarise(runs, 'https://example.com/', 'mobile')['scores']['performance']
        self.assertEqual((score['median'], score['min'], score['max']), (90, 80, 100))

    def test_a_null_category_is_omitted_not_zeroed(self):
        """Recording an uncomputable category as 0 would drag a median toward a
        failure that never happened."""
        lab = psi.lab_of({'categories': {'performance': {'score': None},
                                         'seo': {'score': 1.0}}})
        self.assertNotIn('performance', lab['scores'])
        self.assertEqual(lab['scores']['seo'], 100)


class RedirectedFieldData(unittest.TestCase):
    """PSI answers about wherever the URL redirected to, and does not say so.

    Asked about https://www.bbc.co.uk/ on 2026-08-01, PSI returned field data
    whose `id` was https://www.bbc.com/ with `origin_fallback` false, so nothing
    in the response marked the substitution. The two disagree on CLS by two
    categories (0.08 against 0.00), so reporting it as "this URL" hands someone
    a real number about a site they did not ask about. Silent and plausible,
    like every other bug in this file.
    """

    @staticmethod
    def _payload(subject, fallback=False):
        return {'lighthouseResult': {'fetchTime': 't1', 'categories': {}, 'audits': {}},
                'loadingExperience': {
                    'id': subject, 'origin_fallback': fallback,
                    'metrics': {'CUMULATIVE_LAYOUT_SHIFT_SCORE':
                                {'percentile': 8, 'category': 'FAST'}}}}

    def test_the_subject_is_carried_out_of_the_response(self):
        scope, _, subject = psi.field_of(self._payload('https://www.bbc.com/'))
        self.assertEqual(scope, 'url')
        self.assertEqual(subject, 'https://www.bbc.com/')

    def test_a_redirect_is_named_rather_than_called_this_url(self):
        result = psi.summarise(
            [{'scores': {}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://www.bbc.co.uk/', 'mobile', 'url',
            {'CUMULATIVE_LAYOUT_SHIFT_SCORE': {'percentile': 8, 'category': 'FAST'}},
            'https://www.bbc.com/')
        text = render.result(result)
        self.assertIn('https://www.bbc.com/', text)
        self.assertIn('redirects', text)
        self.assertNotIn('— this URL', text)

    def test_the_same_url_is_still_called_this_url(self):
        result = psi.summarise(
            [{'scores': {}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://example.com/', 'mobile', 'url',
            {'CUMULATIVE_LAYOUT_SHIFT_SCORE': {'percentile': 8, 'category': 'FAST'}},
            'https://example.com/')
        # The claim that matters is the absence of a redirect, not the wording
        # used to say so. Asserting the phrase made this fail on a rewrite that
        # changed nothing it was guarding.
        self.assertNotIn('redirect', render.result(result))

    def test_a_trailing_slash_is_not_a_redirect(self):
        """PSI normalises the trailing slash, and calling that a redirect would
        put a scary note on almost every check."""
        result = psi.summarise(
            [{'scores': {}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://example.com', 'mobile', 'url',
            {'CUMULATIVE_LAYOUT_SHIFT_SCORE': {'percentile': 8, 'category': 'FAST'}},
            'https://example.com/')
        self.assertNotIn('redirect', render.result(result))

    def test_origin_fallback_still_says_so(self):
        scope, _, _ = psi.field_of(self._payload('https://x.test/', fallback=True))
        self.assertEqual(scope, 'origin')


class Findings(unittest.TestCase):
    """Three rules, each of which exists because measuring showed it had to.

    Audits are as noisy as the scores they explain. Across three distinct
    analyses of one unchanged page on 2026-08-04, `unused-javascript` claimed
    1200ms, 1800ms and 1200ms off LCP, and TBT savings swung 1450-2750ms. A
    findings tool that quoted one run would be the exact fault this package
    exists to correct, with the aggravating factor that a number attached to an
    instruction sends someone off to do work.
    """

    LHR = {
        'audits': {
            'redirects': {'title': 'Avoid multiple page redirects', 'score': 0,
                          'scoreDisplayMode': 'metricSavings',
                          'description': 'Redirects introduce delays. [Learn more](x)',
                          'metricSavings': {'LCP': 7000, 'FCP': 7000}},
            'unused-javascript': {'title': 'Reduce unused JavaScript', 'score': 0,
                                  'scoreDisplayMode': 'metricSavings',
                                  'description': 'Defer scripts.',
                                  'metricSavings': {'LCP': 1200}},
            'cache-insight': {'title': 'Use efficient cache lifetimes', 'score': 0.5,
                              'scoreDisplayMode': 'binary', 'description': 'Cache.'},
            'largest-contentful-paint': {'title': 'Largest Contentful Paint',
                                         'score': 0, 'scoreDisplayMode': 'numeric'},
            'button-name': {'title': 'Buttons do not have an accessible name',
                            'score': 0, 'scoreDisplayMode': 'binary',
                            'description': 'Name your buttons.'},
            'not-here': {'title': 'Does not apply', 'score': 0,
                         'scoreDisplayMode': 'notApplicable'},
            'ask-a-human': {'title': 'Check this yourself', 'score': 0,
                            'scoreDisplayMode': 'manual'},
        },
        'categories': {
            'performance': {'auditRefs': [
                {'id': 'largest-contentful-paint', 'weight': 25, 'group': 'metrics'},
                {'id': 'first-contentful-paint', 'weight': 10, 'group': 'metrics'},
                {'id': 'redirects', 'weight': 0, 'group': 'diagnostics'},
                {'id': 'unused-javascript', 'weight': 0, 'group': 'diagnostics'},
                {'id': 'cache-insight', 'weight': 0, 'group': 'diagnostics'},
            ]},
            'accessibility': {'auditRefs': [{'id': 'button-name', 'weight': 10}]},
        },
    }

    def _records(self, *overrides):
        """One record per analysis, with per-analysis overrides applied."""
        out = []
        for over in overrides:
            lhr = json.loads(json.dumps(self.LHR))
            for aid, patch in over.items():
                if patch is None:
                    lhr['audits'][aid]['score'] = 1        # passed this time
                else:
                    lhr['audits'][aid].setdefault('metricSavings', {}).update(patch)
            out.append(findings.record(lhr))
        return out

    def test_a_fault_that_did_not_reproduce_is_not_reported(self):
        """It passed once, so it is the instrument moving, not a finding."""
        recs = self._records({}, {'unused-javascript': None}, {})
        got = {f['id'] for f in findings.collect(recs, self.LHR)}
        self.assertIn('redirects', got)
        self.assertNotIn('unused-javascript', got)

    def test_savings_carry_a_median_and_a_spread(self):
        recs = self._records({'unused-javascript': {'LCP': 1200}},
                             {'unused-javascript': {'LCP': 1800}},
                             {'unused-javascript': {'LCP': 1200}})
        f = next(x for x in findings.collect(recs, self.LHR) if x['id'] == 'unused-javascript')
        self.assertEqual(f['savings']['LCP']['median'], 1200)
        self.assertEqual((f['savings']['LCP']['min'], f['savings']['LCP']['max']), (1200, 1800))

    def test_the_metric_audits_are_not_findings(self):
        """They ARE the score. "Fix Largest Contentful Paint" is not advice."""
        got = {f['id'] for f in findings.collect(self._records({}, {}), self.LHR)}
        self.assertNotIn('largest-contentful-paint', got)

    def test_not_applicable_and_manual_are_not_failures(self):
        got = {f['id'] for f in findings.collect(self._records({}, {}), self.LHR)}
        self.assertNotIn('not-here', got)
        self.assertNotIn('ask-a-human', got)

    def test_ranked_by_what_moves_the_score_not_the_biggest_number(self):
        """redirects claims 7000ms on two weighted metrics, unused-js 1200 on
        one, and cache-insight claims nothing at all despite failing."""
        f = findings.collect(self._records({}, {}), self.LHR)
        perf = [x['id'] for x in f if x['category'] == 'performance']
        self.assertEqual(perf[0], 'redirects')
        self.assertEqual(perf[-1], 'cache-insight')

    def test_a_diagnostic_with_no_metric_savings_scores_zero(self):
        f = next(x for x in findings.collect(self._records({}, {}), self.LHR)
                 if x['id'] == 'cache-insight')
        self.assertEqual(f['impact'], 0)

    def test_non_performance_is_ranked_in_score_points_instead(self):
        """Milliseconds mean nothing to the accessibility score, so that
        category is ranked by the points a failure costs it."""
        f = next(x for x in findings.collect(self._records({}, {}), self.LHR)
                 if x['id'] == 'button-name')
        self.assertEqual(f['unit'], 'score-points')
        self.assertEqual(f['impact'], 10)

    def test_categories_are_never_ranked_against_each_other(self):
        f = findings.collect(self._records({}, {}), self.LHR)
        cats = [x['category'] for x in f]
        self.assertEqual(cats, sorted(cats), 'findings must be grouped by category')

    def test_off_page_faults_are_flagged(self):
        """A DNS redirect is real and is not a change to the page. Whoever is
        reading may not be the person who can fix it."""
        f = next(x for x in findings.collect(self._records({}, {}), self.LHR)
                 if x['id'] == 'redirects')
        self.assertTrue(f['off_page'])

    def test_the_learn_more_link_is_stripped(self):
        f = next(x for x in findings.collect(self._records({}, {}), self.LHR)
                 if x['id'] == 'redirects')
        self.assertNotIn('Learn more', f['description'])
        self.assertNotIn('http', f['description'])

    def test_markdown_links_anywhere_become_plain_words(self):
        """Google puts links mid-sentence, not only in a trailing Learn more.
        Stripping only the trailing one left
        "[Optimize LCP](https://developer.chrome.com/...)" in the output."""
        self.assertEqual(
            findings._prose('[Optimize LCP](https://x) by making it discoverable.'),
            'Optimize LCP by making it discoverable.')

    def test_every_learn_clause_goes_not_just_learn_more(self):
        """Google writes "Learn how to minify CSS" as well as "Learn more", and
        a fixed list missed it, so the report printed the dangling phrase."""
        for text, want in (
                ('Minifying CSS reduces payload. Learn how to minify CSS.',
                 'Minifying CSS reduces payload.'),
                ('Defer offscreen images. Learn why this matters.',
                 'Defer offscreen images.')):
            self.assertEqual(findings._prose(text), want)

    def test_the_word_learn_mid_sentence_is_left_alone(self):
        """Only a trailing clause goes. Stripping the word everywhere would eat
        real prose."""
        keep = 'A description that legitimately says learners are welcome.'
        self.assertEqual(findings._prose(keep), keep)

    def test_a_dangling_learn_more_is_removed_not_just_unlinked(self):
        """Keeping the words while dropping the link leaves a phrase pointing at
        nothing, which reads as deliberate and is worse than raw markdown."""
        self.assertEqual(
            findings._prose('Redirects add delays. [Learn more](https://y).'),
            'Redirects add delays.')

    def test_weights_are_read_from_the_response_not_hardcoded(self):
        """Lighthouse changes them between versions."""
        w = findings.weights_of(self.LHR)
        self.assertEqual(w['largest-contentful-paint'], 25)
        self.assertEqual(w['button-name'], 10)

    def test_the_report_says_savings_do_not_add_up(self):
        """Two fixes claiming time off LCP overlap. The output must not invite
        anyone to sum them."""
        self.assertIn('do not add up', render.FINDINGS_NOTE)

    def test_nothing_failing_says_so_rather_than_printing_an_empty_list(self):
        self.assertIn('Nothing failed', render.findings([], 'https://x.test/'))


class CruxErrorClassification(unittest.TestCase):
    """Three refusals that mean genuinely different things and need different
    fixes. Conflating them sends people to fix the wrong thing."""

    def test_404_is_no_data_not_a_failure(self):
        problem = crux._classify(404, {}, 'https://small.example/')
        self.assertEqual(problem.reason, 'no_data')

    def test_restricted_key_is_told_apart_from_a_disabled_api(self):
        problem = crux._classify(
            403, {'error': {'message': 'Requests to this API '
                            'chromeuxreport.googleapis.com method '
                            'google.chrome.uxreport.v1.ChromeUXReport.QueryRecord '
                            'are blocked.'}}, 'https://example.com/')
        self.assertEqual(problem.reason, 'restricted')

    def test_not_enabled_is_recognised_and_keeps_googles_link(self):
        url = ('https://console.developers.google.com/apis/api/'
               'chromeuxreport.googleapis.com/overview?project=1234')
        problem = crux._classify(
            403, {'error': {'message': 'Chrome UX Report API has not been used in '
                            f'project 1234 before or it is disabled. Enable it by '
                            f'visiting {url} then retry.'}}, 'https://example.com/')
        self.assertEqual(problem.reason, 'not_enabled')
        self.assertEqual(problem.console_url, url)

    def test_not_enabled_advice_admits_it_might_just_be_propagation(self):
        """Enablement is not atomic across endpoints. A tool that asserts the
        API is off sends someone to enable an API that is already enabled, and
        they conclude the tool is broken."""
        problem = crux._classify(
            403, {'error': {'message': 'Chrome UX Report API has not been used in '
                            'project 1 before or it is disabled.'}},
            'https://example.com/')
        self.assertIn('propagating', problem.hint)


def crux_fixture(lcp_p75, phases, image_share, ttfb=None, rtt=None):
    """A crux.record() shaped like the real thing, from measured origins."""
    labels = ['server response', 'load delay', 'download', 'render delay']
    return {
        'scope': 'origin',
        'period': {'first': '2026-07-06', 'last': '2026-08-02'},
        'metrics': {
            'LCP': {'p75': lcp_p75,
                    'histogram': [{'start': 0, 'end': 2500, 'density': 0.9},
                                  {'start': 2500, 'end': 4000, 'density': 0.07},
                                  {'start': 4000, 'end': None, 'density': 0.03}]},
            **({'TTFB': {'p75': ttfb, 'histogram': []}} if ttfb is not None else {}),
            **({'RTT': {'p75': rtt, 'histogram': []}} if rtt is not None else {}),
        },
        'lcp_phases': dict(zip(labels, phases)),
        'shares': {'LCP element': {'image': image_share, 'text': 1 - image_share}},
    }


# Measured live on 2026-08-05. Stack Overflow is the fixture that matters: its
# phases total 2.8x its LCP and describe an eighth of its visits, so any code
# that treats the four as a decomposition of the headline number produces
# obvious nonsense against it and quiet nonsense everywhere else.
STACKOVERFLOW = crux_fixture(1488, [428, 3295, 208, 173], 0.126, ttfb=492, rtt=106)
GOV_UK = crux_fixture(671, [322, 191, 121, 164], 0.0199, ttfb=300, rtt=85)
BBC = crux_fixture(917, [457, 249, 85, 213], 0.82, ttfb=428, rtt=81)
NYTIMES = crux_fixture(2292, [741, 690, 161, 279], 0.411, ttfb=579, rtt=88)


class LcpPhasesDoNotSumToLcp(unittest.TestCase):
    """Each phase is its own 75th percentile over the image-LCP visits, and the
    LCP p75 is over all of them. Two populations, and percentiles do not add.

    Measured across twelve origins on 2026-08-05, the sum missed the LCP every
    time and in both directions: theguardian.com -40ms, gov.uk +127ms,
    nytimes.com -421ms, stackoverflow.com +2616ms. Treating the four as a
    decomposition of the headline number is wrong on every real site tested,
    and it fails silently, because four numbers under a heading look like a
    breakdown whatever they add up to.
    """

    def test_shares_are_of_the_phase_total_not_of_the_lcp(self):
        out = lcp.explain(STACKOVERFLOW)
        delay = next(p for p in out['phases'] if p['label'] == 'load delay')
        # 3295 / 4104 = 80%. Against the LCP of 1488 it would be 221%, which is
        # the arithmetic a reader assumes is happening unless told otherwise.
        self.assertAlmostEqual(delay['share'], 3295 / 4104, places=6)
        self.assertLessEqual(sum(p['share'] for p in out['phases']), 1.0000001)

    def test_the_gap_is_reported_in_both_directions(self):
        self.assertEqual(lcp.explain(STACKOVERFLOW)['gap'], 4104 - 1488)
        self.assertEqual(lcp.explain(NYTIMES)['gap'], 1871 - 2292)

    def test_the_text_states_both_totals_and_refuses_to_reconcile_them(self):
        text = render.lcp(lcp.explain(STACKOVERFLOW), 'https://stackoverflow.com/')
        self.assertIn('4.10 s', text)          # the phase total
        self.assertIn('1.49 s', text)          # the LCP it does not explain
        self.assertIn('do not add up to it', text)

    def test_the_html_page_says_it_too(self):
        page = report.build(
            [{'url': 'https://stackoverflow.com/', 'analyses': 2, 'requested': 2,
              'cached_replays': 0, 'short': False, 'scores': {}, 'metrics': {},
              'field': {}, 'field_scope': None}],
            field={'https://stackoverflow.com/': {'record': STACKOVERFLOW}},
            inline_fonts=False)
        self.assertIn('do not add up to it', page)

    def test_the_page_and_the_text_format_a_phase_the_same_way(self):
        """A table reading 3295 ms beside a report reading 3.29 s is two
        renderers disagreeing in miniature, which is how they start
        disagreeing about what a number means."""
        analysis = lcp.explain(STACKOVERFLOW)
        text = render.lcp(analysis, 'https://stackoverflow.com/')
        page = report.build(
            [{'url': 'https://stackoverflow.com/', 'analyses': 2, 'requested': 2,
              'cached_replays': 0, 'short': False, 'scores': {}, 'metrics': {},
              'field': {}, 'field_scope': None}],
            field={'https://stackoverflow.com/': {'record': STACKOVERFLOW}},
            inline_fonts=False)
        for phase in analysis['phases']:
            shown = render.duration('x', phase['ms'])
            self.assertIn(shown, text)
            self.assertIn(html.escape(shown, quote=False), page)


class LcpPhasesDescribeOnlyImageVisits(unittest.TestCase):
    """Every sub-part is named `largest_contentful_paint_image_*` and CrUX
    publishes them whatever the split. gov.uk's largest element is text for 98%
    of visits and the four phases still come back, describing the other 2%.

    A breakdown headed "where the LCP time goes" with nothing qualifying it
    describes one visit in fifty as though it were all of them.
    """

    def test_a_minority_is_flagged_as_one(self):
        self.assertTrue(lcp.explain(GOV_UK)['minority'])
        self.assertTrue(lcp.explain(STACKOVERFLOW)['minority'])
        self.assertFalse(lcp.explain(BBC)['minority'])

    def test_the_warning_leads_when_it_is_a_minority(self):
        text = render.lcp(lcp.explain(GOV_UK), 'https://www.gov.uk/')
        self.assertIn('MOST VISITS ARE NOT DESCRIBED', text)
        self.assertIn('2%', text)

    def test_the_share_is_stated_even_when_it_is_a_majority(self):
        text = render.lcp(lcp.explain(BBC), 'https://www.bbc.co.uk/')
        self.assertNotIn('MOST VISITS ARE NOT DESCRIBED', text)
        self.assertIn('82%', text)

    def test_the_short_form_beside_the_other_field_data_carries_it_too(self):
        """render.crux_record prints the same four phases in a smaller block.
        It used to print them bare, which is where a reader would meet them
        first."""
        text = render.crux_record(GOV_UK, 'https://www.gov.uk/')
        self.assertIn('MOST VISITS ARE NOT DESCRIBED', text)

    def test_the_html_page_carries_it_too(self):
        page = report.build(
            [{'url': 'https://www.gov.uk/', 'analyses': 2, 'requested': 2,
              'cached_replays': 0, 'short': False, 'scores': {}, 'metrics': {},
              'field': {}, 'field_scope': None}],
            field={'https://www.gov.uk/': {'record': GOV_UK}}, inline_fonts=False)
        self.assertIn('MOST VISITS ARE NOT DESCRIBED', page)


class LcpPhaseReading(unittest.TestCase):
    def test_phases_stay_in_timeline_order_not_size_order(self):
        """They are sequential. Sorted by size they stop being a timeline, and
        'load delay' before 'server response' is a load nobody can picture."""
        out = lcp.explain(STACKOVERFLOW)
        self.assertEqual([p['label'] for p in out['phases']],
                         ['server response', 'load delay', 'download', 'render delay'])

    def test_the_longest_phase_is_named(self):
        self.assertEqual(lcp.explain(STACKOVERFLOW)['dominant'], 'load delay')
        self.assertEqual(lcp.explain(BBC)['dominant'], 'server response')

    def test_the_rating_comes_from_the_histogram_bins_not_a_hardcoded_number(self):
        """The thresholds are Google's and are already in the response. A second
        copy here would be a number that rots without failing."""
        rec = crux_fixture(3000, [1, 1, 1, 1], 0.9)
        self.assertEqual(lcp.explain(rec)['lcp']['rating'], 'needs improvement')
        # Same p75, bins moved: the rating must follow the response.
        rec['metrics']['LCP']['histogram'][0]['end'] = 4000
        rec['metrics']['LCP']['histogram'][1] = {'start': 4000, 'end': 6000}
        self.assertEqual(lcp.explain(rec)['lcp']['rating'], 'good')

    def test_the_two_server_response_figures_are_not_conflated(self):
        """One is every navigation, the other only the image ones. On nytimes
        they differ by 162ms, which is a fact about the site."""
        out = lcp.explain(NYTIMES)
        self.assertEqual(out['ttfb']['all_navigations'], 579)
        self.assertEqual(out['ttfb']['image_lcp_navigations'], 741)
        text = render.lcp(out, 'https://www.nytimes.com/')
        self.assertIn('579 ms', text)
        self.assertIn('741 ms', text)


class LcpWithNothingToExplain(unittest.TestCase):
    """Most sites have no field data, and some have field data but no image-LCP
    navigations. Those are different answers and neither is a failure."""

    def test_no_phases_is_explained_rather_than_left_blank(self):
        rec = {'scope': 'origin', 'metrics': {'LCP': {'p75': 900, 'histogram': []}},
               'lcp_phases': {}, 'shares': {}}
        out = lcp.explain(rec)
        self.assertFalse(out['available'])
        self.assertEqual(out['reason'], 'no_phases')
        text = render.lcp(out, 'https://x.test/')
        self.assertIn('not a fault', text)
        self.assertIn('900 ms', text)

    def test_no_lcp_at_all_says_there_is_no_field_data(self):
        out = lcp.explain({'scope': 'origin', 'metrics': {}, 'lcp_phases': {}})
        self.assertEqual(out['reason'], 'no_lcp')
        self.assertIn('anonymity threshold', render.lcp(out, 'https://x.test/'))

    def test_a_missing_image_share_does_not_claim_one(self):
        rec = crux_fixture(900, [100, 100, 100, 100], 0.5)
        del rec['shares']['LCP element']
        out = lcp.explain(rec)
        self.assertIsNone(out['minority'])
        self.assertIn('did not say what share',
                      render.lcp(out, 'https://x.test/'))


def spread(median, low, high):
    return {'median': median, 'min': low, 'max': high}


def measurement(analyses=3, scores=None, metrics=None, found=(), version='13.0.0'):
    return {'url': 'https://x.test/', 'strategy': 'mobile', 'analyses': analyses,
            'recorded': '2026-08-01T09:00:00+0000',
            'scores': scores or {}, 'metrics': metrics or {},
            'lighthouse_version': version,
            'findings': [{'id': i, 'title': i.replace('-', ' '),
                          'category': 'performance', 'impact': 100} for i in found]}


class AChangeSmallerThanTheNoiseIsNotAChange(unittest.TestCase):
    """The figures here are from one unchanged page measured three times on
    2026-08-04: performance ran 27 to 37, TBT ran 824ms to 3.05s.

    A tool comparing medians alone would call that page improved or regressed
    depending on the afternoon, and would be equally confident either way. This
    is the test that stops it.
    """

    def test_overlapping_ranges_are_not_a_change(self):
        v = compare.verdict(spread(33, 27, 37), spread(36, 30, 40), 3, 3, True)
        self.assertEqual(v['verdict'], 'no measurable change')

    def test_touching_ranges_are_not_a_change_either(self):
        """A single shared point is still an overlap. Anything else makes the
        verdict turn on one sample landing on a boundary."""
        v = compare.verdict(spread(33, 27, 37), spread(45, 37, 50), 3, 3, True)
        self.assertEqual(v['verdict'], 'no measurable change')

    def test_disjoint_ranges_are_a_change_and_get_a_direction(self):
        v = compare.verdict(spread(33, 27, 37), spread(60, 55, 65), 3, 3, True)
        self.assertEqual(v['verdict'], 'better')

    def test_a_score_falling_is_worse_and_a_metric_falling_is_better(self):
        """Up is good for a score out of 100 and bad for every duration here.
        One direction rule for both would be right half the time."""
        down = (spread(60, 55, 65), spread(33, 27, 37), 3, 3)
        self.assertEqual(compare.verdict(*down, higher_is_better=True)['verdict'],
                         'worse')
        self.assertEqual(compare.verdict(*down, higher_is_better=False)['verdict'],
                         'better')

    def test_a_single_analysis_gets_no_verdict_at_all(self):
        """One analysis has min == max, so two of them always look disjoint.
        Without this guard, two anecdotes that differ report as a real change.
        """
        v = compare.verdict(spread(33, 33, 33), spread(60, 60, 60), 1, 1, True)
        self.assertEqual(v['verdict'], 'unknown')
        self.assertIn('single analysis', v['why'])

    def test_one_side_being_a_single_analysis_is_enough_to_refuse(self):
        v = compare.verdict(spread(33, 27, 37), spread(60, 60, 60), 3, 1, True)
        self.assertEqual(v['verdict'], 'unknown')

    def test_the_guaranteed_change_is_smaller_than_the_median_change(self):
        """Both are reported and they are not the same number. The medians
        differing is the headline; the ranges only guarantee the gap between
        their nearest edges, and that is the one to quote to someone who will
        hold you to it."""
        v = compare.verdict(spread(33, 27, 37), spread(60, 55, 65), 3, 3, True)
        self.assertEqual(v['median_change'], 27)
        self.assertEqual(v['at_least'], 55 - 37)
        self.assertLess(v['at_least'], abs(v['median_change']))


class ComparingTwoMeasurements(unittest.TestCase):
    def test_the_noisy_real_world_case_reports_nothing_moved(self):
        before = measurement(scores={'performance': spread(33, 27, 37)},
                             metrics={'TBT': spread(1800, 824, 3050)})
        after = measurement(scores={'performance': spread(36, 30, 40)},
                            metrics={'TBT': spread(1500, 900, 2600)})
        out = compare.results(before, after)
        self.assertEqual(out['scores']['performance']['verdict'],
                         'no measurable change')
        self.assertEqual(out['metrics']['TBT']['verdict'], 'no measurable change')
        text = render.comparison(out)
        self.assertIn('no measurable change', text)
        for word in ('improved', 'regression', 'faster', 'slower'):
            self.assertNotIn(word, text.lower())

    def test_a_lighthouse_version_change_is_flagged_as_a_confound(self):
        """It reweights and revises audits between versions, so it moves a
        score without the page moving. It is the one confound that looks
        exactly like a result."""
        out = compare.results(measurement(version='12.0.0'),
                              measurement(version='13.0.0'))
        self.assertTrue(any('Lighthouse changed' in n for n in out['notes']))

    def test_the_same_version_is_not_flagged(self):
        self.assertEqual(compare.results(measurement(), measurement())['notes'], [])

    def test_a_metric_missing_from_one_side_is_skipped_not_guessed(self):
        out = compare.results(measurement(metrics={'LCP': spread(2, 1, 3)}),
                              measurement(metrics={'LCP': spread(2, 1, 3),
                                                   'CLS': spread(0.1, 0, 0.2)}))
        self.assertIn('LCP', out['metrics'])
        self.assertNotIn('CLS', out['metrics'])

    def test_the_comparison_is_json_serialisable(self):
        json.dumps(compare.results(measurement(), measurement()))


class ComparingFindings(unittest.TestCase):
    """A finding is only reported when it fails in EVERY distinct analysis, and
    that threshold makes the two directions unequal. Something newly listed
    failed every time it was looked at. Something that dropped off passed at
    least once, which is weaker than fixed."""

    def test_findings_are_split_three_ways(self):
        out = compare.findings(
            [{'id': 'redirects', 'title': 'Avoid redirects'},
             {'id': 'unused-css', 'title': 'Unused CSS'}],
            [{'id': 'unused-css', 'title': 'Unused CSS'},
             {'id': 'uses-http2', 'title': 'Use HTTP/2'}])
        self.assertEqual([f['id'] for f in out['gone']], ['redirects'])
        self.assertEqual([f['id'] for f in out['new']], ['uses-http2'])
        self.assertEqual([f['id'] for f in out['remaining']], ['unused-css'])

    def test_the_report_never_calls_a_dropped_finding_fixed(self):
        out = compare.results(measurement(found=['redirects']), measurement())
        text = render.comparison(out)
        self.assertIn('No longer failing every analysis', text)
        self.assertIn('not the same as fixed', text)
        self.assertNotIn('Fixed', text)


class Baselines(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._saved = os.environ.get('PAGESPEED_CONFIG_DIR')
        os.environ['PAGESPEED_CONFIG_DIR'] = self._dir

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('PAGESPEED_CONFIG_DIR', None)
        else:
            os.environ['PAGESPEED_CONFIG_DIR'] = self._saved

    def test_a_baseline_round_trips(self):
        config.save_baseline('https://x.test/', 'mobile',
                             compare.snapshot(measurement()))
        self.assertIsNotNone(config.get_baseline('https://x.test/', 'mobile'))

    def test_mobile_and_desktop_are_separate_baselines(self):
        """Comparing a mobile run against a desktop baseline would report the
        difference between two simulated devices as the effect of a change."""
        config.save_baseline('https://x.test/', 'mobile',
                             compare.snapshot(measurement()))
        self.assertIsNone(config.get_baseline('https://x.test/', 'desktop'))

    def test_saving_a_baseline_does_not_touch_the_key(self):
        """Separate files on purpose. The key is secret and changes almost
        never; baselines are not secret and change constantly."""
        config.save({'api_key': 'AIza-secret', 'urls': ['https://x.test/']})
        config.save_baseline('https://x.test/', 'mobile',
                             compare.snapshot(measurement()))
        self.assertEqual(config.api_key(), 'AIza-secret')
        self.assertNotIn('AIza-secret', config.baselines_path().read_text())

    def test_the_snapshot_drops_the_prose_it_does_not_need(self):
        """A full result carries Google's remediation text and the offending
        elements for every finding. None of that answers whether something
        moved, and all of it would be stored forever."""
        rich = measurement(found=['redirects'])
        rich['findings'][0]['description'] = 'x' * 5000
        snap = compare.snapshot(rich)
        self.assertNotIn('description', snap['findings'][0])
        self.assertEqual(snap['findings'][0]['title'], 'redirects')

    def test_a_corrupt_baselines_file_is_survived_not_fatal(self):
        config.baselines_path().write_text('{ not json')
        self.assertEqual(config.load_baselines(), {})

    def test_clearing_reports_whether_there_was_anything_to_clear(self):
        config.save_baseline('https://x.test/', 'mobile',
                             compare.snapshot(measurement()))
        self.assertTrue(config.clear_baseline('https://x.test/', 'mobile'))
        self.assertFalse(config.clear_baseline('https://x.test/', 'mobile'))


class TheSkillIsInstallable(unittest.TestCase):
    """Two things this got wrong, both of which looked fine.

    The command a skill answers to comes from its DIRECTORY name in most
    clients, not from anything inside the file. This shipped as `skill/SKILL.md`,
    so copying the folder in gave a skill answering to the wrong name. It
    installed, it loaded, and it was wrong, which is the kind of fault nobody
    reports.

    Then the instructions replacing that were written for one client, with its
    filesystem paths hardcoded. This server is for any assistant that speaks
    MCP, and the server itself is registered by handing the assistant a prompt
    and letting it find its own config. The skill has to work the same way, or
    it is documentation for a fraction of the people who installed it.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    SKILL = 'pagespeed-insights-read'

    def skill_dir(self):
        return self.ROOT / 'skills' / self.SKILL

    def test_the_folder_is_named_for_the_skill_it_installs_as(self):
        self.assertTrue((self.skill_dir() / 'SKILL.md').is_file())

    def test_the_frontmatter_name_matches_the_folder(self):
        """A mismatch is invisible until someone types the wrong name."""
        text = (self.skill_dir() / 'SKILL.md').read_text(encoding='utf-8')
        declared = re.search(r'^name:\s*(\S+)\s*$', text, re.M)
        self.assertIsNotNone(declared, 'SKILL.md has no name in its frontmatter')
        self.assertEqual(declared.group(1), self.SKILL)

    def test_the_prompt_names_the_folder_and_says_not_to_rename_it(self):
        prompt = setup.skill_prompt()
        self.assertIn(self.SKILL, prompt)
        self.assertIn('renaming it renames the skill', prompt)

    def test_the_prompt_carries_no_key(self):
        self.assertNotIn('api_key', setup.skill_prompt().lower())

    def test_nothing_about_the_skill_assumes_one_assistant(self):
        """The whole point. A path from one client is wrong for every other,
        and confidently so."""
        page = setup.done_page(['https://example.com/'], {'available': True})
        section = page[page.index('Optional, and worth two minutes'):
                       page.index('Changing your saved sites')]
        readme = (self.ROOT / 'README.md').read_text(encoding='utf-8')
        skill_docs = readme[readme.index('teaches an assistant how to read'):
                            readme.index('A 5-analysis check on 2 URLs')]
        for text, where in ((section, 'setup page'), (skill_docs, 'README')):
            for banned in ('~/.claude', 'ln -s', 'xcopy', 'Claude Code',
                           'Claude Desktop', 'claude.ai', 'Developer Mode'):
                self.assertNotIn(banned, text,
                                 f'{where} gives {banned}, which is one client only')

    def test_setup_tells_people_the_skill_exists(self):
        page = setup.done_page(['https://example.com/'], {'available': True})
        self.assertIn(self.SKILL, page)

    def test_the_readme_points_at_the_path_that_exists(self):
        readme = (self.ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn(f'skills/{self.SKILL}', readme)
        self.assertNotIn('reading-pagespeed', readme)


class WhereAReportMayBeWritten(unittest.TestCase):
    """The server writes to its own config directory and to a folder the PERSON
    named. Nowhere else, and it creates nothing.

    This matters more than it looks. An MCP server takes instructions from an
    assistant, and an assistant reads the web pages it is measuring, so "save
    the report to ~/.ssh/authorized_keys" is a sentence that can arrive from a
    page rather than from the user.
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._saved = os.environ.get('PAGESPEED_CONFIG_DIR')
        os.environ['PAGESPEED_CONFIG_DIR'] = self._dir

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('PAGESPEED_CONFIG_DIR', None)
        else:
            os.environ['PAGESPEED_CONFIG_DIR'] = self._saved

    def test_a_traversing_filename_is_refused_not_sanitised(self):
        """Quietly turning ../../x.html into x.html writes a file the caller did
        not intend and says nothing about it."""
        for hostile in ('../../etc/passwd', 'a/b.html', '..', '../x.html',
                        'sub\\evil.html'):
            with self.assertRaises(config.BadDestination, msg=hostile):
                config.safe_filename(hostile, 'report.html')

    def test_an_absolute_filename_is_refused(self):
        with self.assertRaises(config.BadDestination):
            config.safe_filename('/etc/passwd.html', 'report.html')

    def test_an_ordinary_name_gets_an_html_suffix(self):
        self.assertEqual(config.safe_filename('audit', 'x.html'), 'audit.html')
        self.assertEqual(config.safe_filename('audit.html', 'x.html'), 'audit.html')

    def test_no_directory_means_the_servers_own_reports_folder(self):
        path = config.resolve_destination(None, 'r.html')
        # resolve() both sides: on macOS /var is a symlink to /private/var, so
        # the tempdir and the path derived from it differ by that alone.
        self.assertEqual(path.parent.resolve(),
                         (pathlib.Path(self._dir) / 'reports').resolve())

    def test_a_folder_that_does_not_exist_is_refused_rather_than_created(self):
        """A typo would otherwise make a stray folder and put the report
        somewhere the user will not think to look."""
        missing = os.path.join(self._dir, 'not-there')
        with self.assertRaises(config.BadDestination):
            config.resolve_destination(missing, 'r.html')
        self.assertFalse(os.path.exists(missing))

    def test_a_file_where_a_folder_should_be_is_refused(self):
        f = pathlib.Path(self._dir) / 'afile'
        f.write_text('x')
        with self.assertRaises(config.BadDestination):
            config.resolve_destination(str(f), 'r.html')

    def test_an_existing_folder_is_accepted(self):
        d = pathlib.Path(self._dir) / 'out'
        d.mkdir()
        self.assertEqual(config.resolve_destination(str(d), 'r.html').parent,
                         d.resolve())

    def test_nothing_is_ever_overwritten(self):
        """Someone comparing a before against an after has two reports they both
        want, and silently replacing the first loses the thing they were about
        to compare against."""
        d = pathlib.Path(self._dir) / 'out'
        d.mkdir()
        (d / 'r.html').write_text('the first one')
        second = config.resolve_destination(str(d), 'r.html')
        self.assertNotEqual(second.name, 'r.html')
        self.assertEqual((d / 'r.html').read_text(), 'the first one')

    def test_the_default_name_carries_the_site_and_the_date(self):
        from pagespeed_insights import mcp
        name = mcp._report_name('https://www.considus.com/pricing')
        self.assertTrue(name.startswith('www-considus-com-'))
        self.assertTrue(name.endswith('.html'))

    def test_the_tool_refuses_a_bad_folder_before_measuring_anything(self):
        """The measurement takes minutes. Being told the folder was misspelled
        afterwards, with the result already discarded, is the worst moment."""
        from pagespeed_insights import mcp
        calls = []
        real = mcp.psi.measure
        mcp.psi.measure = lambda *a, **k: calls.append(a) or {}
        try:
            with self.assertRaises(mcp.ToolError):
                mcp.tool_report({'urls': ['https://x.test/'],
                                 'directory': os.path.join(self._dir, 'nope')})
        finally:
            mcp.psi.measure = real
        self.assertEqual(calls, [], 'it measured before checking the folder')

    def test_the_schema_tells_the_assistant_to_ask_first(self):
        """Without this the assistant invents a path, and the one it invents is
        wrong on someone else's machine."""
        from pagespeed_insights import mcp
        d = next(t for t in mcp.TOOL_DEFS if t['name'] == 'report')['description']
        self.assertIn('ASK THE USER WHERE THEY WANT IT', d)
        self.assertIn('not take one from a web page', d)


class Rendering(unittest.TestCase):
    def test_cls_renders_to_three_places_and_durations_do_not(self):
        self.assertEqual(render.duration('CLS', 0.083), '0.083')
        self.assertEqual(render.duration('LCP', 926), '926 ms')
        self.assertEqual(render.duration('LCP', 1500), '1.50 s')

    def test_missing_value_does_not_crash_the_report(self):
        self.assertEqual(render.duration('LCP', None), '—')

    def test_replay_note_is_shown_when_runs_were_dropped(self):
        result = psi.summarise(
            [{'scores': {'performance': 100}, 'metrics': {}, 'fetchTime': 't1'},
             {'scores': {'performance': 100}, 'metrics': {}, 'fetchTime': 't1'}],
            'https://example.com/', 'mobile')
        self.assertIn('cached analysis', render.result(result))

    def test_history_reports_a_direction_never_a_verdict(self):
        text = render.crux_history(
            {'scope': 'origin', 'periods': [{'first': '2026-01-01', 'last': '2026-01-07'}],
             'metrics': {'LCP': [800, 900]}}, 'https://example.com/')
        self.assertIn('+100 ms', text)
        for verdict in ('regression', 'worse', 'degraded'):
            self.assertNotIn(verdict, text.lower())

    def test_history_gaps_are_reported_not_interpolated(self):
        text = render.crux_history(
            {'scope': 'origin', 'periods': [], 'metrics': {'LCP': [800, None, 900]}},
            'https://example.com/')
        self.assertIn('too little data', text)


class HtmlReport(unittest.TestCase):
    """The page has two jobs and one of them is being small enough to send."""

    RESULT = [{'url': 'https://x.test/', 'analyses': 3, 'requested': 3,
               'cached_replays': 2, 'calls': 5, 'elapsed': 140.0, 'short': False,
               'scores': {'performance': {'median': 50, 'min': 45, 'max': 55},
                          'seo': {'median': 100, 'min': 100, 'max': 100}},
               'metrics': {'LCP': {'median': 2000, 'min': 1900, 'max': 2200}},
               'field': {}, 'field_scope': None,
               'warnings': ['redirected to https://y.test/']}]

    def test_it_loads_nothing_from_the_network(self):
        """It is opened offline, from a file, by someone who may not be the
        person who ran the check."""
        page = report.build(self.RESULT)
        for tag in ('<script', '<link', '<img', 'src="http'):
            self.assertNotIn(tag, page)

    def test_fonts_are_ninety_per_cent_of_it_so_the_mcp_drops_them(self):
        """145KB of base64 is about 37,000 tokens. On disk that is free; through
        an assistant it is most of a context window spent on typography."""
        big, small = report.build(self.RESULT), report.build(self.RESULT, inline_fonts=False)
        self.assertGreater(len(big), 8 * len(small))
        self.assertNotIn('@font-face', small)

    def test_the_mark_and_palette_survive_either_way(self):
        """A page with no logo does not look like it came from anywhere."""
        for page in (report.build(self.RESULT),
                     report.build(self.RESULT, inline_fonts=False)):
            self.assertIn('<svg', page)
            self.assertIn('--stellar', page)

    def test_the_spread_reaches_the_page(self):
        """A prettier page that quietly rounded away the spread would be worse
        than no page."""
        page = report.build(self.RESULT, inline_fonts=False)
        self.assertIn('45', page)
        self.assertIn('55', page)

    def test_a_score_with_no_spread_says_so_rather_than_looking_certain(self):
        page = report.build(self.RESULT, inline_fonts=False)
        self.assertIn('no spread', page)

    def test_googles_warning_is_carried_verbatim(self):
        self.assertIn('redirected to https://y.test/',
                      report.build(self.RESULT, inline_fonts=False))

    def test_no_field_data_is_explained_not_left_blank(self):
        page = report.build(self.RESULT, inline_fonts=False,
                            field={'https://x.test/':
                                   {'unavailable': {'reason': 'no_data'}}})
        self.assertIn('not a fault', page)

    def test_a_url_cannot_inject_markup(self):
        hostile = [dict(self.RESULT[0], url='https://x.test/<script>alert(1)</script>')]
        page = report.build(hostile, inline_fonts=False)
        self.assertNotIn('<script>alert', page)


class Config(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k)
                       for k in ('PAGESPEED_CONFIG_DIR', 'PAGESPEED_API_KEY')}
        os.environ['PAGESPEED_CONFIG_DIR'] = self._dir
        os.environ.pop('PAGESPEED_API_KEY', None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_no_key_is_a_supported_state_not_an_error(self):
        self.assertIsNone(config.api_key())

    def test_environment_beats_the_file(self):
        config.save({'api_key': 'from-file'})
        os.environ['PAGESPEED_API_KEY'] = 'from-env'
        self.assertEqual(config.api_key(), 'from-env')

    def test_explicit_beats_everything(self):
        os.environ['PAGESPEED_API_KEY'] = 'from-env'
        self.assertEqual(config.api_key('explicit'), 'explicit')

    @unittest.skipIf(os.name == 'nt',
                     'Windows has no POSIX file modes. os.open ignores the mode '
                     'argument and the file reports 0666, so protection comes '
                     'from the profile directory ACL instead. Asserting 0600 '
                     'here would be asserting a guarantee the platform does not '
                     'make.')
    def test_settings_are_written_owner_only(self):
        path = config.save({'api_key': 'x', 'urls': []})
        self.assertEqual(pathlib.Path(path).stat().st_mode & 0o777, 0o600)

    def test_settings_land_in_the_configured_directory(self):
        """The cross-platform half of the guarantee. Wherever the key ends up,
        it must be inside the directory we chose and not somewhere incidental
        like the working directory or the repo."""
        path = pathlib.Path(config.save({'api_key': 'x', 'urls': []})).resolve()
        self.assertEqual(path.parent, pathlib.Path(self._dir).resolve())
        self.assertTrue(path.is_file())

    def test_a_corrupt_settings_file_does_not_crash_the_tool(self):
        config.settings_path().write_text('{ this is not json')
        self.assertEqual(config.load(), {})


class McpProtocol(unittest.TestCase):
    """The server must answer, and must never die on one bad request."""

    def setUp(self):
        from pagespeed_insights import mcp
        self.mcp = mcp
        self.sent = []
        self._real_send = mcp._send
        mcp._send = self.sent.append

    def tearDown(self):
        self.mcp._send = self._real_send

    def _call(self, request):
        self.sent.clear()
        self.mcp.handle(request)
        return self.sent

    def test_initialize_echoes_the_requested_protocol(self):
        out = self._call({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                          'params': {'protocolVersion': '2099-01-01'}})
        self.assertEqual(out[0]['result']['protocolVersion'], '2099-01-01')

    def test_every_tool_declares_a_schema(self):
        out = self._call({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        tools = out[0]['result']['tools']
        self.assertEqual({t['name'] for t in tools},
                         {'check_pagespeed', 'report', 'diagnose_page',
                          'field_data', 'explain_lcp', 'compare', 'diagnose'})
        for tool in tools:
            self.assertEqual(tool['inputSchema']['type'], 'object')
            self.assertTrue(tool['description'])

    def test_unknown_tool_is_an_error_not_a_crash(self):
        out = self._call({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                          'params': {'name': 'nope', 'arguments': {}}})
        self.assertIn('error', out[0])

    def test_a_bad_url_is_refused_before_any_network_call(self):
        out = self._call({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                          'params': {'name': 'check_pagespeed',
                                     'arguments': {'urls': ['file:///etc/passwd']}}})
        self.assertTrue(out[0]['result']['isError'])

    def test_an_oversized_request_is_refused_rather_than_started(self):
        out = self._call({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                          'params': {'name': 'check_pagespeed',
                                     'arguments': {'urls': ['https://a.example/',
                                                            'https://b.example/'],
                                                   'strategy': 'both', 'runs': 10}}})
        self.assertTrue(out[0]['result']['isError'])

    def test_runs_must_be_a_whole_number(self):
        for bad in (0, 11, True, 2.5, 'five'):
            out = self._call({'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
                              'params': {'name': 'check_pagespeed',
                                         'arguments': {'urls': ['https://a.example/'],
                                                       'runs': bad}}})
            self.assertTrue(out[0]['result'].get('isError'), f'runs={bad!r} was allowed')

    def test_a_slow_tool_still_beats_while_it_is_blocked(self):
        """The reason every client run timed out.

        The per-poll callback fires BETWEEN PageSpeed calls. It cannot fire
        during one, and a single call blocks while Lighthouse runs, which on a
        slow page is exactly the site being measured. Without a keepalive there
        was 58 seconds of silence on a real 2-run report and the client gave up.
        """
        import time as _t
        beats = []
        real_send, self.mcp._send = self.mcp._send, beats.append
        real_ka = self.mcp.KEEPALIVE_SECONDS
        self.mcp.KEEPALIVE_SECONDS = 0.05
        try:
            with self.mcp._Keepalive('tok', 3) as alive:
                alive.note(1, 3, 'measuring')
                # Wait for the beats, not for the clock. The thread beats every
                # KEEPALIVE_SECONDS, but how many times it is scheduled inside a
                # fixed sleep is a fact about the machine rather than about the
                # keepalive, and a loaded CI runner managed one. The deadline is
                # long enough that reaching it means the beats never came at
                # all, which is the thing worth failing on.
                deadline = _t.monotonic() + 5
                while len(beats) < 2 and _t.monotonic() < deadline:
                    _t.sleep(0.01)
        finally:
            self.mcp.KEEPALIVE_SECONDS = real_ka
            self.mcp._send = real_send
        self.assertGreaterEqual(len(beats), 2, 'no keepalive while blocked')
        self.assertTrue(all(b['method'] == 'notifications/progress' for b in beats))
        self.assertEqual(beats[-1]['params']['message'], 'measuring',
                         'the beat should carry the last real message, not an '
                         'invented one')

    def test_no_progress_token_means_no_keepalive(self):
        """Beating at a client that never asked for progress is noise on a
        stream where noise is fatal."""
        import time as _t
        beats = []
        real_send, self.mcp._send = self.mcp._send, beats.append
        real_ka = self.mcp.KEEPALIVE_SECONDS
        self.mcp.KEEPALIVE_SECONDS = 0.05
        try:
            with self.mcp._Keepalive(None, 3):
                _t.sleep(0.2)
        finally:
            self.mcp.KEEPALIVE_SECONDS = real_ka
            self.mcp._send = real_send
        self.assertEqual(beats, [])

    def test_the_keepalive_stops_before_the_result_is_sent(self):
        """A beat arriving after its own response is a stray message about a
        request the client has already closed."""
        import time as _t
        alive = self.mcp._Keepalive('tok', 1)
        with alive:
            pass
        self.assertFalse(alive._thread.is_alive())

    def test_every_writer_shares_one_lock(self):
        """Two threads writing to stdout interleave into one corrupt line, and
        the client reports the server dying for no visible reason."""
        # setUp replaces mcp._send, so inspect the real one this class saved
        # rather than the stand-in.
        import inspect
        self.assertIn('_WRITE', inspect.getsource(self._real_send))

    def test_notifications_get_no_reply(self):
        self.assertEqual(self._call({'jsonrpc': '2.0',
                                     'method': 'notifications/initialized'}), [])

    def test_results_are_json_serialisable(self):
        """Every result crosses the wire as JSON. Anything that will not encode
        kills the stream rather than failing one call."""
        out = self._call({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/list'})
        json.dumps(out[0])


class ThisMachineMarker(unittest.TestCase):
    """The marker is the only thing telling a reader which of several blocks is
    theirs, so a wrong one sends them to another platform's commands.

    This is a regression test. rerun_blocks() once carried its own copy of the
    marker string, and rewording it moved two of the three and left the copy
    behind, rendering two different markers on one page. The string now lives in
    _block() alone, and this fails if it is ever duplicated back out.
    """

    def test_one_block_is_marked_per_section(self):
        for name in ('darwin', 'linux', 'nt'):
            with as_platform(name):
                for section, fn in (('rerun', setup.rerun_blocks),
                                    ('usage', setup.usage_blocks)):
                    marked = [h for h in headings(fn()) if 'this machine' in h]
                    self.assertEqual(len(marked), 1, f'{section} on {name}: {marked}')

    def test_the_marker_follows_the_platform(self):
        with as_platform('nt'):
            self.assertIn('Windows (this machine)', headings(setup.usage_blocks()))
        for name in ('darwin', 'linux'):
            with as_platform(name):
                self.assertIn('macOS and Linux (this machine)',
                              headings(setup.usage_blocks()))

    def test_windows_has_no_install_block_to_mark(self):
        """There is nothing to install on Windows, so an unmarked pair is the
        correct output. path_note carries the explanation instead."""
        with as_platform('nt'):
            self.assertEqual(
                [h for h in headings(setup.install_blocks()) if 'this machine' in h], [])
            self.assertIn('nothing here to install', visible(setup.path_note()))


class ForeignPaths(unittest.TestCase):
    """Every block prints a path, and only one of them describes this computer.
    A block headed Windows that says cd "/Users/someone" is worse than no
    example, because it looks copyable."""

    def test_only_the_current_platform_gets_the_real_path(self):
        with as_platform('darwin'):
            self.assertIn(setup.HERE, setup.install_blocks())      # the macOS block
            self.assertIn('path/to/pagespeed-insights-mcp',
                          setup.install_blocks())                  # the Linux one
        with as_platform('nt'):
            # Neither install block is about this machine, so neither may claim it.
            self.assertNotIn(setup.HERE, setup.install_blocks())

    def test_the_windows_block_never_shows_a_posix_path(self):
        for name in ('darwin', 'linux'):
            with as_platform(name):
                windows_block = commands(setup.usage_blocks())[1]
                self.assertNotIn(setup.HERE, windows_block)
                self.assertIn(r'path\to\pagespeed-insights-mcp', windows_block)


class RerunCommand(unittest.TestCase):
    """Reopening setup failed three different ways, each silently.

    `cd` to a path on another drive does nothing in cmd.exe, leaving the next
    line to run somewhere else. An unquoted interpreter path stops at the space
    in `C:\\Program Files`. And once quoted, PowerShell needs the call operator,
    because a statement beginning with a quoted string prints the path instead
    of running it. None of the three raises an error.
    """

    def test_there_is_no_cd(self):
        """setup.py resolves everything from __file__, so the working directory
        never mattered and the cd was pure risk."""
        for name in ('darwin', 'linux', 'nt'):
            with as_platform(name):
                for block in commands(setup.rerun_blocks()):
                    self.assertNotIn('cd ', block, f'{name}: {block}')

    def test_both_paths_are_quoted_where_one_contains_a_space(self):
        with as_platform('nt'):
            windows_block = commands(setup.rerun_blocks())[1]
            self.assertIn(f'"{sys.executable}"', windows_block)
            self.assertIn(f'"{setup.HERE}\\setup.py"', windows_block)

    def test_powershell_gets_the_call_operator_and_posix_does_not(self):
        with as_platform('nt'):
            self.assertTrue(commands(setup.rerun_blocks())[1].startswith('& "'))
        with as_platform('darwin'):
            posix_block = commands(setup.rerun_blocks())[0]
            self.assertFalse(posix_block.startswith('&'))
            self.assertIn(f'"{sys.executable}"', posix_block)

    def test_it_is_one_command_not_two(self):
        for name in ('darwin', 'nt'):
            with as_platform(name):
                for block in commands(setup.rerun_blocks()):
                    self.assertEqual(len(block.splitlines()), 1, block)


class InstallAndUsageAreSeparate(unittest.TestCase):
    """Stacked in one block with a blank line between them, there was no line
    that was unambiguously the last one, and the note underneath had to point at
    a specific command. It pointed at a usage example instead."""

    def test_the_install_block_ends_at_the_echo_line(self):
        """path_note calls it 'the echo line' and describes 'the first two
        lines'. Both stop being true if usage examples return to this block."""
        for name in ('darwin', 'linux'):
            with as_platform(name):
                for block in commands(setup.install_blocks()):
                    lines = block.splitlines()
                    self.assertEqual(len(lines), 3, block)
                    self.assertTrue(lines[2].startswith('echo '), lines[2])

    def test_usage_blocks_install_nothing(self):
        for name in ('darwin', 'linux', 'nt'):
            with as_platform(name):
                for block in commands(setup.usage_blocks()):
                    for verb in ('mkdir', 'ln -s', 'echo '):
                        self.assertNotIn(verb, block, f'{name}: {block}')


class PathNote(unittest.TestCase):
    """Three states, and the wrong one is worse than none: telling someone to
    skip the line that is the reason the command does not work yet."""

    def setUp(self):
        self._path = os.environ.get('PATH', '')

    def tearDown(self):
        os.environ['PATH'] = self._path

    def test_says_to_leave_the_line_out_when_the_folder_is_already_found(self):
        os.environ['PATH'] = os.path.expanduser('~/.local/bin')
        with as_platform('darwin'):
            self.assertIn('leave that line out', visible(setup.path_note()))

    def test_says_the_line_is_the_one_that_matters_when_it_is_not(self):
        os.environ['PATH'] = '/usr/bin'
        with as_platform('darwin'):
            note = visible(setup.path_note())
            self.assertIn('makes the command work', note)
            self.assertNotIn('leave that line out', note)


class VisibleCopy(unittest.TestCase):
    def test_no_em_or_en_dashes_reach_the_reader(self):
        """House convention, and the reason it needs a test is that most of this
        copy only renders on a platform nobody is reading it from."""
        for name in ('darwin', 'linux', 'nt'):
            with as_platform(name):
                for fragment in (setup.install_blocks(), setup.usage_blocks(),
                                 setup.rerun_blocks(), setup.path_note(),
                                 setup.install_prompt(), setup.EXAMPLE_PROMPTS):
                    text = visible(fragment)
                    self.assertNotIn('\u2014', text, f'{name}: {text[:80]}')
                    self.assertNotIn('\u2013', text, f'{name}: {text[:80]}')


class SetupPageRenders(unittest.TestCase):
    """The whole page, on the real platform only. done_page reaches the config
    directory, and a faked Windows cannot build a WindowsPath here."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._saved = os.environ.get('PAGESPEED_CONFIG_DIR')
        os.environ['PAGESPEED_CONFIG_DIR'] = self._dir

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('PAGESPEED_CONFIG_DIR', None)
        else:
            os.environ['PAGESPEED_CONFIG_DIR'] = self._saved

    def test_both_pages_render_with_and_without_saved_state(self):
        for page in (setup.form_page(),
                     setup.form_page('something went wrong', {'api_key': 'k'}),
                     setup.done_page([], {'available': False, 'reason': 'x',
                                          'hint': 'y', 'console_url': ''}),
                     setup.done_page(['https://example.com'], {'available': True})):
            self.assertTrue(page.startswith('<!doctype html>'))
            self.assertIn('</html>', page)

    def test_the_saved_urls_appear_on_the_done_page(self):
        self.assertIn('https://example.com',
                      visible(setup.done_page(['https://example.com'],
                                              {'available': True})))

    def test_the_install_prompt_carries_no_key(self):
        """It is meant to be pasted into an assistant, so it must stay safe to
        paste even when a key is saved."""
        config.save({'api_key': 'AIzaSECRET', 'urls': []})
        self.assertNotIn('AIzaSECRET', setup.install_prompt())
        self.assertNotIn('AIzaSECRET', setup.done_page([], {'available': True}))


class Annotations(unittest.TestCase):
    """A directory submission is rejected without a title and the applicable
    hint, and a wrong hint is worse than a missing one, because a client uses
    readOnlyHint to decide what it may run without asking."""

    def setUp(self):
        from pagespeed_insights import mcp
        self.mcp = mcp

    def test_titles_match_the_tool_list_exactly(self):
        self.assertEqual(set(self.mcp.TITLES),
                         {t['name'] for t in self.mcp.TOOLS})

    def test_read_only_set_names_real_tools(self):
        names = {t['name'] for t in self.mcp.TOOLS}
        self.assertEqual(self.mcp._READ_ONLY - names, set())

    def test_read_only_tools_omit_the_hints_that_mean_nothing(self):
        for name in self.mcp._READ_ONLY:
            ann = self.mcp._annotations(name)
            self.assertTrue(ann['readOnlyHint'])
            self.assertNotIn('destructiveHint', ann)
            self.assertNotIn('idempotentHint', ann)

    def test_report_is_not_read_only_because_it_can_write_a_file(self):
        # The only tool here that touches the disk. An annotation describes the
        # tool and not the call, so passing directory or filename is the case
        # that decides it.
        ann = self.mcp._annotations('report')
        self.assertFalse(ann['readOnlyHint'])
        self.assertIn('destructiveHint', ann)

    def test_saving_a_report_destroys_nothing(self):
        # config.resolve_destination picks a free name rather than overwriting,
        # which is what makes this additive and also what makes it non-idempotent.
        ann = self.mcp._annotations('report')
        self.assertFalse(ann['destructiveHint'])
        self.assertFalse(ann['idempotentHint'])


class AnnotationsOverStdio(unittest.TestCase):
    """The real thing, in a subprocess, over the wire.

    TOOL_DEFS copies a named list of keys, so a tool can carry a title and
    annotations in TOOLS and still arrive at the client without them, and
    nothing fails when it does. Asserting on the module cannot see that. This
    launches the server the way a client does and reads what comes back."""

    def _tools_list(self):
        server = pathlib.Path(__file__).resolve().parent.parent / 'mcp_server.py'
        payload = '\n'.join(json.dumps(r) for r in [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                        'clientInfo': {'name': 'test', 'version': '0'}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ]) + '\n'
        out = subprocess.run([sys.executable, str(server)], input=payload,
                             capture_output=True, text=True, timeout=60).stdout
        for line in out.splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get('id') == 2:
                return msg['result']['tools']
        self.fail('no tools/list reply came back')

    def test_every_tool_arrives_with_a_title_and_annotations(self):
        for tool in self._tools_list():
            self.assertTrue(tool.get('title'), tool['name'])
            self.assertIn('annotations', tool)
            self.assertIn('readOnlyHint', tool['annotations'])

    def test_the_hints_that_arrive_are_the_ones_declared(self):
        by_name = {t['name']: t for t in self._tools_list()}
        self.assertTrue(by_name['check_pagespeed']['annotations']['readOnlyHint'])
        self.assertFalse(by_name['report']['annotations']['readOnlyHint'])
        self.assertFalse(by_name['report']['annotations']['destructiveHint'])


if __name__ == '__main__':
    unittest.main()
