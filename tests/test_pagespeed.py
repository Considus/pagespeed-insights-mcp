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
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import setup                                                      # noqa: E402
from pagespeed_insights import config, crux, findings, psi, render  # noqa: E402
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

    def test_progress_reports_distinct_collected_not_calls_made(self):
        seen = []
        state = {'i': 0}
        script = [self._payload(1000, 't1'), self._payload(1000, 't1'),
                  self._payload(1100, 't2')]

        def fake_fetch(url, strategy, key, **_):
            p = script[min(state['i'], len(script) - 1)]
            state['i'] += 1
            return p

        real, psi.fetch = psi.fetch, fake_fetch
        try:
            psi.measure('https://x.test/', runs=2, key='k', sleep=lambda s: None,
                        progress=lambda d, t, u, s: seen.append(d))
        finally:
            psi.fetch = real
        self.assertEqual(seen, [1, 2])            # not [1, 2, 3]


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
                         {'check_pagespeed', 'diagnose_page', 'field_data', 'diagnose'})
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


if __name__ == '__main__':
    unittest.main()
