"""Offline tests. Nothing here touches the network or a real API key.

The two regression tests at the top are the point of this file. Both bugs were
found by running against the live API and both were silent — they produced a
plausible number rather than an error, which is the only kind of bug that
matters in a measurement tool.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pagespeed_insights import config, crux, psi, render          # noqa: E402
from pagespeed_insights.errors import CruxUnavailable             # noqa: E402


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

    def test_settings_are_written_owner_only(self):
        path = config.save({'api_key': 'x', 'urls': []})
        self.assertEqual(pathlib.Path(path).stat().st_mode & 0o777, 0o600)

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
                         {'check_pagespeed', 'field_data', 'diagnose'})
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


if __name__ == '__main__':
    unittest.main()
