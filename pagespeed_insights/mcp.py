"""The MCP face — newline-delimited JSON-RPC 2.0 over stdin/stdout.

    python3 -m pagespeed_insights.mcp

Register it with an MCP client and an assistant can measure a page properly
instead of quoting one noisy run at you. It holds no measurement logic: every
tool here calls the same functions the CLI calls and formats with the same
renderer, so the two faces cannot disagree about what a number means.

TWO THINGS THAT BREAK AN MCP SERVER, BOTH GUARDED HERE.

stdout is the protocol. A single stray `print` corrupts the JSON-RPC stream and
the client reports the server dying for no visible reason. Nothing in this
package prints to stdout except this module, and the one function that does
write to stderr (the CLI's progress) is not reachable from here.

Long calls get killed. A five-run check on two URLs is several minutes, which
is past the default timeout of most clients. This emitted progress
notifications whenever the caller supplied a token, on the understanding that
clients reset the timeout when one arrives.

THAT UNDERSTANDING WAS WRONG, and it cost the tool. Measured 2026-08-29:
Claude's desktop app and Claude Code both attach `_meta.progressToken` only
when the caller passes an onprogress handler, no tool-call path in either
passes one, so no token ever arrives and the keepalive never starts. Claude
Code's own per-server timeout schema says the rest outright — "Hard wall-clock
limit per call; progress notifications do not extend it." The limit measured
was 60 seconds, against a default check budgeted at 300.

Whether some client once honoured it is not something these measurements can
say, and the note below about notification CADENCE still stands on its own
evidence. What they do say is that a multi-minute answer cannot depend on it.
So the work no longer happens inside the call: see the jobs section further
down. The notifications stay because they are correct and cost one JSON line
for a client that does ask.

HOW OFTEN IS THE WHOLE PROBLEM, and getting it wrong made the feature useless
while looking implemented. Notifications originally fired only when a new
distinct analysis landed. Google produces one about once a minute, so the
heartbeat beat at roughly the interval it existed to survive: measured on
2026-08-05, a two-run report emitted its two notifications 58 seconds apart and
every run through a real client timed out. The tool looked broken and the
guard was the reason.

Two things fix it, and both are needed. The measurement callback now fires on
every poll rather than every new analysis, which is every 15s BETWEEN calls.
And a keepalive thread beats every 10s regardless, because the callback cannot
fire DURING a call and a single PageSpeed call blocks while Lighthouse runs,
which on a slow page is exactly the site being measured. Same report after:
19 notifications, longest silence 10.3s.

That thread is the one place this package writes to stdout from anywhere but
the main loop, so every writer goes through a lock. Two interleaved writes are
one corrupt line and a server that appears to die for no reason, which is the
first hazard above arriving through the fix for the second.
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid

from . import __version__, compare, config, crux, lcp, psi, render, report
from .errors import CruxUnavailable, PageSpeedError

SERVER_NAME = 'pagespeed-insights'
DEFAULT_PROTOCOL = '2025-06-18'

# These keep the worst case to something a client will wait for, and refuse
# clearly rather than starting work that cannot finish.
MAX_RUNS = 10
MAX_URLS = 6
# The cost is TIME, not requests. Google re-analyses a URL about once a minute
# and replays the cached result in between, so five distinct analyses take
# roughly 150 seconds however often you ask. A cap counting requests would have
# been meaningless once collection replaced counting.
MAX_SECONDS = 15 * 60


class ToolError(Exception):
    """A refusal to report as tool output, not a crash."""


def _urls(raw):
    urls = raw or config.default_urls()
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        raise ToolError('No URLs given and none saved. Pass urls, or run setup.')
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise ToolError('urls must be a list of strings.')
    urls = [u.strip() for u in urls if u.strip()]
    for u in urls:
        if not u.startswith(('http://', 'https://')):
            raise ToolError(f'{u!r} is not an http(s) URL. PageSpeed fetches pages '
                            'over the public web; it cannot reach a local path.')
    if len(urls) > MAX_URLS:
        raise ToolError(f'{len(urls)} URLs is more than this server will do in one '
                        f'call (limit {MAX_URLS}). Split it across calls.')
    return urls


# ----------------------------------------------------------------------------
# Jobs: how a measurement outlives the call that asked for it
# ----------------------------------------------------------------------------
# The progress notifications further down are correct, and against the clients
# measured on 2026-08-29 they are also inert. Claude's desktop app and Claude
# Code both attach `_meta.progressToken` only when the caller supplies an
# onprogress handler, and no tool-call path in either supplies one, so the
# token never arrives and the keepalive never starts. Claude Code's own
# per-server timeout schema settles the rest in its own words: "Hard wall-clock
# limit per call; progress notifications do not extend it."
#
# The limit measured there was 60 seconds, twice. This server budgets a default
# check at 300. Google re-analyses a URL about once a minute, so five distinct
# analyses take about 150 seconds and two already take 68 — measured against
# example.com the same day, 8.4s for one and 68.3s for two. Inside one call
# this server can therefore honestly collect exactly ONE analysis, which is the
# single noisy run the whole package exists to refuse.
#
# So work that will not fit does not happen inside the call. It starts a worker
# and hands back a job id, every call returns in well under a second, and the
# caller polls check_status. Nothing then depends on what the client's timeout
# is, or on whether it honours progress at all.
#
# State goes to a file as well as memory because the job can outlive the
# PROCESS. The desktop app reaps an idle stdio server and spawns a fresh one,
# and more than one can be alive at once — measured, four distinct server PIDs
# in one afternoon. A poll that lands in a different process finds the answer
# on disk or it finds nothing.

# Seconds of work to attempt inside one tool call. Under the smallest client
# timeout measured, with room left for the reply itself.
INLINE_BUDGET = 45
# What a collection really costs, which is not its budget. psi.measure's budget
# is a ceiling; this is the expectation, and the difference is what decides
# whether a call can be answered inline at all.
FIRST_ANALYSIS_SECONDS = 12
EXTRA_ANALYSIS_SECONDS = 60
# Threads, not requests. Running more at once finishes none of them sooner,
# because the wait is Google's re-analysis interval rather than our own rate.
MAX_ACTIVE_JOBS = 4
# A finished job is kept this long so a poll can be repeated, then deleted.
JOB_TTL_SECONDS = 3600
# A running job touches its file on every poll of Google, which is every ~15s,
# EXCEPT while a single call blocks. That block is bounded by psi.fetch's own
# 180s timeout, so anything quiet for longer than this has died rather than
# slowed down.
JOB_STALE_SECONDS = 240

_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _job_estimate(jobs, runs):
    """Seconds a collection will really take.

    The first analysis arrives as fast as Lighthouse runs. Every one after it
    waits for Google to produce a genuinely new one, which is about a minute
    and which asking harder does not change.
    """
    return jobs * (FIRST_ANALYSIS_SECONDS
                   + max(0, runs - 1) * EXTRA_ANALYSIS_SECONDS)


def _duration(seconds):
    seconds = int(round(seconds))
    if seconds < 120:
        return f'{seconds} seconds'
    return f'{seconds // 60} to {seconds // 60 + 1} minutes'


def _inline_budget():
    """How long this server may spend inside one call before deferring.

    The client's timeout is not in the protocol and cannot be read from the
    environment either: the variables a stdio server inherits from the desktop
    app were measured down to seven, none of them a timeout. So it is a
    default, and anyone whose client waits longer says so rather than having
    this guess — PAGESPEED_INLINE_BUDGET, or inline_budget_seconds in
    settings.json.
    """
    raw = os.environ.get('PAGESPEED_INLINE_BUDGET', '').strip()
    if not raw:
        raw = config.load().get('inline_budget_seconds')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return INLINE_BUDGET
    return value if value > 0 else INLINE_BUDGET


def _job_path(job_id):
    return config.jobs_dir() / f'{job_id}.json'


def _job_write(record):
    """Atomically, because a poll can land in another process mid-write.

    NEVER RAISES, and the directory lookup is inside the try for that reason.
    The file is what lets ANOTHER process answer a poll, which is a
    convenience. The measurement itself lives in memory in this process, so a
    disk that will not take the file must cost the poll and nothing else.
    Somewhere with an unwritable HOME, a container being the obvious one,
    config.jobs_dir() raises while creating the directory, and leaving that
    outside the try took the whole job down with it.
    """
    try:
        path = _job_path(record['id'])
        tmp = path.with_name(path.name + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(record, f)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def _job_read(job_id):
    with _JOBS_LOCK:
        record = _JOBS.get(job_id)
    if record is not None:
        return dict(record)
    try:
        with open(_job_path(job_id), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _job_files():
    try:
        return sorted(config.jobs_dir().glob('*.json'))
    except OSError:
        return []


def _job_prune():
    """Drop what is past its keep-by. Called when a job starts, so a server
    nobody is using writes nothing and deletes nothing."""
    now = time.time()
    for path in _job_files():
        try:
            if now - path.stat().st_mtime > JOB_TTL_SECONDS:
                path.unlink()
        except OSError:
            pass
    with _JOBS_LOCK:
        for job_id in [k for k, v in _JOBS.items()
                       if now - v.get('updated', 0) > JOB_TTL_SECONDS]:
            _JOBS.pop(job_id, None)


def _latest_job_id():
    """The most recently started job this machine knows about, so a caller
    that lost the id is not stuck."""
    records = []
    with _JOBS_LOCK:
        records.extend(dict(v) for v in _JOBS.values())
    for path in _job_files():
        try:
            with open(path, encoding='utf-8') as f:
                records.append(json.load(f))
        except (OSError, ValueError):
            continue
    best, best_at = None, None
    for record in records:
        started = record.get('started') or 0
        if best_at is None or started > best_at:
            best, best_at = record.get('id'), started
    return best


def _start_job(tool, label, estimate, work):
    """Hand the work to a thread and hand the caller a receipt."""
    _job_prune()
    with _JOBS_LOCK:
        running = [j for j in _JOBS.values() if j.get('status') == 'running']
        if len(running) >= MAX_ACTIVE_JOBS:
            raise ToolError(
                f'{len(running)} measurements are already running and this '
                f'server will not start more than {MAX_ACTIVE_JOBS} at once. '
                'Collect one with check_status first. They are slow because '
                'Google re-analyses a URL about once a minute, so starting '
                'more of them at once does not finish any of them sooner.')
        job_id = uuid.uuid4().hex[:8]
        now = time.time()
        record = {'id': job_id, 'tool': tool, 'label': label,
                  'status': 'running', 'started': now, 'updated': now,
                  'finished': None, 'estimate': estimate,
                  'progress': 'starting', 'done': 0, 'total': 0,
                  'content': None, 'error': None, 'hint': ''}
        _JOBS[job_id] = record
    try:
        _job_write(dict(record))
    except Exception:
        # Registered but never started is a slot consumed forever, and
        # MAX_ACTIVE_JOBS then refuses work for a job that is not running.
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
        raise

    def _publish(update, finishing=False):
        # THE FILE IS WRITTEN BEFORE MEMORY IS UPDATED, and the order is the
        # point. The file is what another process reads, so a run that
        # published to memory first would have a window where this process
        # says done and every other one still says running. Leading with the
        # durable copy means no reader ever sees the answer go backwards. The
        # write stays outside the lock, because a poll must not wait on disk.
        with _JOBS_LOCK:
            live = _JOBS.get(job_id)
            if live is None:
                return
            snapshot = dict(live)
        snapshot.update(update)
        snapshot['updated'] = time.time()
        if finishing:
            snapshot['finished'] = snapshot['updated']
        _job_write(snapshot)
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id] = snapshot

    def note(done, total, message):
        _publish({'done': done, 'total': total, 'progress': message})

    def run():
        try:
            update = {'status': 'done', 'content': work(note),
                      'progress': 'complete'}
        except (ToolError, PageSpeedError) as e:
            update = {'status': 'failed',
                      'error': e.message if isinstance(e, PageSpeedError) else str(e),
                      'hint': getattr(e, 'hint', '') or ''}
        except Exception as e:
            # A worker dying silently would leave the caller polling a job that
            # never finishes, which is worse than any error text.
            update = {'status': 'failed',
                      'error': f'Unexpected error in {tool}: {e}'}
        _publish(update, finishing=True)

    threading.Thread(target=run, daemon=True).start()
    return [{'type': 'text', 'text':
             f'Started job {job_id} — {label}.\n'
             f'About {_duration(estimate)}. Google re-analyses a URL only about '
             'once a minute, so this is time rather than requests and asking '
             'harder will not speed it up.\n\n'
             f'Collect it with check_status, job_id "{job_id}". Poll about every '
             '15 seconds until it reports done. The work carries on between '
             'polls. If this server is restarted while it runs the job is lost, '
             'and check_status says so rather than returning nothing.'}]


def _run_or_defer(tool, label, estimate, work, progress):
    """Inline when it fits inside one call, a job when it does not."""
    if estimate <= _inline_budget():
        def report(done, total, message):
            if progress:
                progress(done, total, message)
        return work(report)
    return _start_job(tool, label, estimate, work)


def tool_check_pagespeed(args, progress=None):
    urls = _urls(args.get('urls'))
    strategy = args.get('strategy') or 'mobile'
    if strategy not in ('mobile', 'desktop', 'both'):
        raise ToolError("strategy must be 'mobile', 'desktop' or 'both'.")
    strategies = ('mobile', 'desktop') if strategy == 'both' else (strategy,)

    runs = args.get('runs', 5)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 1 to {MAX_RUNS}.')

    # Each URL and strategy collects independently, and each can take up to its
    # own budget. Refuse up front rather than start something the client will
    # abandon halfway through.
    jobs = len(urls) * len(strategies)
    budget = max(psi.MIN_BUDGET, runs * psi.BUDGET_PER_ANALYSIS)
    worst_case = jobs * budget
    if worst_case > MAX_SECONDS:
        raise ToolError(
            f'that is {jobs} page/strategy combinations at up to '
            f'{budget // 60} minutes each, so up to {worst_case // 60} minutes, '
            f'over this server\'s limit of {MAX_SECONDS // 60}. Google re-analyses '
            'a URL about once a minute, so this is time rather than requests and '
            'asking harder will not speed it up. Reduce runs, drop "both", or '
            'check fewer URLs.')

    key = config.api_key()
    target = jobs * runs

    def work(report_progress):
        done = [0]

        def tick(distinct, _target, url, strat, fresh):
            # Counts ANALYSES but reports on every CALL. The count has to
            # reflect measurements collected rather than requests spent, and
            # the report has to arrive often enough to show the thing is alive,
            # and those are different rates.
            if fresh:
                done[0] += 1
            report_progress(done[0], target,
                            f'{url} [{strat}] {distinct}/{runs} distinct analyses')

        reports, results = [], []
        for url in urls:
            for strat in strategies:
                res = psi.measure(url, strat, runs, key, progress=tick)
                results.append(res)
                reports.append(render.result(res))

        payload = {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                   'key_in_use': bool(key), 'results': results}
        return [{'type': 'text',
                 'text': '\n\n'.join(reports) + '\n\n' + render.NOISE_NOTE},
                {'type': 'text', 'text': json.dumps(payload, indent=2)}]

    label = (f"{', '.join(urls)} [{'/'.join(strategies)}], "
             f'{runs} distinct analyses')
    return _run_or_defer('check_pagespeed', label,
                         _job_estimate(jobs, runs), work, progress)


def tool_diagnose_page(args, progress=None):
    """What is failing, ranked by what fixing it is worth."""
    urls = _urls(args.get('urls'))
    runs = args.get('runs', 3)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 2 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 2 to {MAX_RUNS}. Below '
                        'two there is nothing to check a finding against, so a '
                        'one-off failure would be reported as a fact.')
    key = config.api_key()

    def work(report_progress):
        done = [0]

        def tick(distinct, _t, url, strat, fresh):
            # Counts analyses, reports on every call. See psi.measure.
            if fresh:
                done[0] += 1
            report_progress(done[0], len(urls) * runs,
                            f'{url} [{strat}] {distinct}/{runs} distinct analyses')

        texts, payload = [], {}
        for url in urls:
            res = psi.measure(url, 'mobile', runs, key, progress=tick,
                              with_findings=True)
            found = res.get('findings') or []
            texts.append(render.findings(found, url))
            for w in res.get('warnings') or []:
                texts.append(f'  Google warns: {w}')
            payload[url] = {'analyses': res['analyses'], 'findings': found,
                            'lighthouse_version': res.get('lighthouse_version'),
                            'warnings': res.get('warnings') or []}

        return [{'type': 'text',
                 'text': '\n\n'.join(texts) + '\n\n' + render.FINDINGS_NOTE},
                {'type': 'text', 'text': json.dumps(payload, indent=2)}]

    label = f"{', '.join(urls)} [mobile], findings over {runs} distinct analyses"
    return _run_or_defer('diagnose_page', label,
                         _job_estimate(len(urls), runs), work, progress)


def _report_name(url):
    """A file name from the URL and the date, so two sites do not collide."""
    host = urllib.parse.urlsplit(url).netloc or 'report'
    safe = ''.join(c if c.isalnum() or c == '-' else '-' for c in host).strip('-')
    return f"{safe or 'report'}-{time.strftime('%Y-%m-%d')}.html"


def tool_report(args, progress=None):
    """The whole answer in one call, and the page to send on.

    Three tools existed and a person wanting "how is my site and what should I
    do" had to call all three and stitch the results. That is work this should
    be doing for them.
    """
    urls = _urls(args.get('urls'))
    runs = args.get('runs', 3)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 2 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 2 to {MAX_RUNS}. Below '
                        'two there is nothing to check a finding against.')
    want_html = args.get('html', True)
    save_to = args.get('directory')
    filename = args.get('filename')
    # Writing is opt-in. A tool that saved a file every time it ran would be
    # leaving things on someone's disk they never asked for.
    writing = bool(save_to or filename)
    if writing:
        # Resolved BEFORE the measurement, which takes minutes. Finding out
        # the folder was misspelled after a three-minute wait, with the result
        # already discarded, is the worst possible moment to be told.
        try:
            destination = config.resolve_destination(
                save_to, filename,
                default_name=_report_name(urls[0]) if urls else 'report.html')
        except config.BadDestination as e:
            raise ToolError(str(e))
    key = config.api_key()

    def work(report_progress):
        done = [0]

        def tick(distinct, _t, url, strat, fresh):
            # Counts analyses, reports on every call. See psi.measure.
            if fresh:
                done[0] += 1
            report_progress(done[0], len(urls) * runs,
                            f'{url} [{strat}] {distinct}/{runs} distinct analyses')

        texts, results, field, findings_by_url = [], [], {}, {}
        for url in urls:
            res = psi.measure(url, 'mobile', runs, key, progress=tick, with_findings=True)
            results.append(res)
            findings_by_url[url] = res.get('findings') or []
            texts.append(render.result(res))

            try:
                rec = crux.record(url, key)
                field[url] = {'record': rec}
                texts.append(render.crux_record(rec, url))
            except CruxUnavailable as e:
                field[url] = {'unavailable': {'reason': e.reason, 'message': e.message}}
                # Not an error. Most sites have no field data and saying so plainly
                # is the point, because a gap beside a good lab score reads as proof.
                texts.append(f'{url}  [real users]\n  {e.message}\n  {e.hint}')

            texts.append(render.findings(findings_by_url[url], url))
            for warning in res.get('warnings') or []:
                texts.append(f'  Google warns: {warning}')

        blocks = [{'type': 'text',
                   'text': '\n\n'.join(texts) + '\n\n' + render.NOISE_NOTE
                           + '\n' + render.FINDINGS_NOTE}]
        if want_html or writing:
            # Fonts on when it lands on disk, off when it crosses the wire. They are
            # 145KB of base64, 90% of the page and about 37,000 tokens, which is
            # most of a context window spent on typography. On disk that is free and
            # the page keeps its typography anywhere it is opened. Same renderer
            # either way: a second one is how two versions start disagreeing about a
            # number.
            page = report.build(results, field=field, findings_by_url=findings_by_url,
                                generated=time.strftime('%d %B %Y'),
                                inline_fonts=bool(writing))
            if writing:
                try:
                    destination.write_text(page, encoding='utf-8')
                except OSError as e:
                    raise ToolError(f'Could not write {destination}: {e}')
                blocks.insert(0, {'type': 'text',
                                  'text': f'Report written to {destination} '
                                          f'({len(page.encode()) // 1024}KB, '
                                          'self-contained, opens offline).'})
            if want_html and not writing:
                blocks.append({'type': 'text', 'text': page})
        blocks.append({'type': 'text', 'text': json.dumps(
            {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
             'results': results, 'field': field}, indent=2)})
        return blocks

    label = (f"{', '.join(urls)} [mobile], scores, field data and findings "
             f'over {runs} distinct analyses')
    return _run_or_defer('report', label,
                         _job_estimate(len(urls), runs), work, progress)


def tool_field_data(args, progress=None):
    urls = _urls(args.get('urls'))
    want_history = bool(args.get('history'))
    key = config.api_key()

    texts, payload = [], {}
    for url in urls:
        entry = {}
        try:
            rec = crux.record(url, key)
            entry['record'] = rec
            texts.append(render.crux_record(rec, url))
        except CruxUnavailable as e:
            entry['unavailable'] = {'reason': e.reason, 'message': e.message,
                                    'hint': e.hint, 'console_url': e.console_url}
            texts.append(f'{url}  [real users]\n  {e.message}\n  {e.hint}')
            payload[url] = entry
            continue

        if want_history:
            try:
                hist = crux.history(url, key)
                entry['history'] = hist
                texts.append(render.crux_history(hist, url))
            except CruxUnavailable as e:
                entry['history_unavailable'] = {'reason': e.reason,
                                                'message': e.message, 'hint': e.hint}
                texts.append(f'  History unavailable: {e.message}\n  {e.hint}')
        payload[url] = entry

    return [{'type': 'text', 'text': '\n\n'.join(texts)},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_explain_lcp(args, progress=None):
    """Which of four phases owns a slow LCP. Field data, so it answers at once."""
    urls = _urls(args.get('urls'))
    key = config.api_key()

    texts, payload = [], {}
    for url in urls:
        try:
            rec = crux.record(url, key)
        except CruxUnavailable as e:
            payload[url] = {'unavailable': {'reason': e.reason,
                                            'message': e.message, 'hint': e.hint}}
            texts.append(f'{url}  [where the LCP time goes]\n  {e.message}\n  {e.hint}')
            continue
        analysis = lcp.explain(rec)
        payload[url] = analysis
        texts.append(render.lcp(analysis, url))

    # Only when there is a breakdown to caveat. Appended to a "no field data"
    # answer it explains the reading of four numbers nobody was shown.
    body = '\n\n'.join(texts)
    if any(a.get('available') for a in payload.values()):
        body += '\n\n' + render.LCP_NOTE
    return [{'type': 'text', 'text': body},
            {'type': 'text', 'text': json.dumps(payload, indent=2)}]


def tool_compare(args, progress=None):
    """Measure now and say whether anything moved since the baseline.

    First call on a URL records the baseline and says so. That is the honest
    shape of the job: there is nothing to compare against until someone has
    measured, changed something, and measured again.
    """
    urls = _urls(args.get('urls'))
    strategy = args.get('strategy') or 'mobile'
    if strategy not in ('mobile', 'desktop'):
        raise ToolError("strategy must be 'mobile' or 'desktop'.")
    runs = args.get('runs', 3)
    if not isinstance(runs, int) or isinstance(runs, bool) or not 2 <= runs <= MAX_RUNS:
        raise ToolError(f'runs must be a whole number from 2 to {MAX_RUNS}. One '
                        'analysis has no spread, so there would be nothing to '
                        'tell a real change from run-to-run noise.')
    replace = bool(args.get('save_baseline'))
    key = config.api_key()

    def work(report_progress):
        done = [0]

        def tick(distinct, _t, url, strat, fresh):
            # Counts analyses, reports on every call. See psi.measure.
            if fresh:
                done[0] += 1
            report_progress(done[0], len(urls) * runs,
                            f'{url} [{strat}] {distinct}/{runs} distinct analyses')

        texts, payload, compared = [], {}, False
        for url in urls:
            res = psi.measure(url, strategy, runs, key, progress=tick, with_findings=True)
            res['recorded'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            snap = compare.snapshot(res)
            baseline = config.get_baseline(url, strategy)

            if baseline is None or replace:
                config.save_baseline(url, strategy, snap)
                payload[url] = {'baseline_recorded': snap}
                texts.append(render.result(res))
                texts.append(
                    f'  Baseline recorded for {url} [{strategy}]'
                    + ('.' if baseline is None else ', replacing the previous one.')
                    + '\n  Make your change, then run this again to see whether it '
                      'moved anything.')
                continue

            compared = True
            result = compare.results(baseline, snap)
            payload[url] = {'comparison': result, 'baseline': baseline, 'now': snap}
            texts.append(render.comparison(result, baseline.get('recorded')))
            texts.append('  The baseline is unchanged, so the next run compares '
                         'against the same starting point. Pass save_baseline to '
                         'move it to this measurement.')

        body = '\n\n'.join(texts)
        if compared:
            body += '\n\n' + render.COMPARE_NOTE
        return [{'type': 'text', 'text': body},
                {'type': 'text', 'text': json.dumps(payload, indent=2)}]

    label = (f"{', '.join(urls)} [{strategy}], compared over "
             f'{runs} distinct analyses')
    return _run_or_defer('compare', label,
                         _job_estimate(len(urls), runs), work, progress)


def tool_check_status(args, progress=None):
    """Collect a measurement that was too long to answer inside one call.

    Returns instantly, always. Whatever the caller's timeout is, this fits
    inside it, which is the whole point of the job it is collecting.
    """
    job_id = args.get('job_id')
    if job_id is not None and not isinstance(job_id, str):
        raise ToolError('job_id must be a string.')
    job_id = (job_id or '').strip()
    if not job_id:
        # Losing the id should not lose the measurement.
        job_id = _latest_job_id()
        if not job_id:
            raise ToolError(
                'No measurement has been started on this server. Start one '
                'with check_pagespeed, report, diagnose_page or compare.')

    record = _job_read(job_id)
    if record is None:
        raise ToolError(
            f'No job {job_id}. A finished job is kept for an hour and then '
            'deleted, and a running one is lost if the server restarts. '
            'Start the measurement again.')

    status = record.get('status')
    started = record.get('started') or time.time()
    elapsed = int(round((record.get('finished') or time.time()) - started))

    if status == 'done':
        # The same blocks the inline path would have returned, so a caller
        # cannot tell from the answer which route it took.
        return record.get('content') or [
            {'type': 'text', 'text': f'Job {job_id} finished with no output.'}]

    if status == 'failed':
        hint = record.get('hint') or ''
        raise ToolError(
            f'Job {job_id} failed after {elapsed}s: '
            f"{record.get('error') or 'no reason recorded'}"
            + (f'\n{hint}' if hint else ''))

    quiet = time.time() - (record.get('updated') or started)
    if quiet > JOB_STALE_SECONDS:
        raise ToolError(
            f'Job {job_id} stopped reporting {int(round(quiet))}s ago and is '
            'lost. The usual cause is this server being restarted while the '
            'measurement ran, which the client does once it has been idle. '
            'Start it again, and keep polling while it runs.')

    remaining = max(0, (record.get('estimate') or 0) - elapsed)
    return [{'type': 'text', 'text':
             f'Job {job_id}: running, {elapsed}s elapsed.\n'
             f"  {record.get('label')}\n"
             f"  {record.get('progress')}\n"
             f"  {record.get('done')} of {record.get('total')} distinct "
             'analyses collected so far.\n'
             f'Roughly {_duration(remaining)} left. Call check_status again in '
             'about 15 seconds.'}]


def tool_diagnose(args, progress=None):
    """What is configured and what actually works, without disclosing the key."""
    key = config.api_key()
    lines = [f'pagespeed-insights-mcp {__version__}',
             f'Settings file: {config.settings_path()}']

    if not key:
        lines += [
            'API key: NONE.',
            '  PageSpeed still answers, on the shared anonymous quota that '
            'everyone else is also using and that is routinely spent. A 429 '
            'from there is Google, not the site being tested.',
            '  The Chrome UX Report needs a key and will refuse entirely.',
            '  Run setup.py to add one.']
        return [{'type': 'text', 'text': '\n'.join(lines)}]

    lines.append('API key: present.')
    try:
        psi.fetch('https://www.google.com/', 'mobile', key, attempts=1, timeout=90)
        lines.append('PageSpeed Insights: working.')
    except PageSpeedError as e:
        lines.append(f'PageSpeed Insights: FAILING — {e.message}')
        if e.hint:
            lines.append(f'  {e.hint}')

    ok, problem = crux.available(key)
    if ok:
        lines.append('Chrome UX Report: working. Real-user data and history are '
                     'available.')
    else:
        lines.append(f'Chrome UX Report: unavailable ({problem.reason}).')
        lines.append(f'  {problem.hint}')
        lines.append('  Everything else works without it.')

    saved = config.default_urls()
    lines.append('Saved URLs: ' + (', '.join(saved) if saved else 'none'))

    # Stale baselines are the quiet failure mode of compare: a comparison
    # against something measured months ago looks exactly like one against
    # something measured this morning.
    baselines = config.load_baselines()
    if baselines:
        lines.append('Baselines held for comparison:')
        for entry in baselines.values():
            lines.append(f"  {entry.get('url')} [{entry.get('strategy')}]"
                         f"  recorded {entry.get('recorded') or 'unknown'}"
                         f", {entry.get('analyses')} analyses")
    else:
        lines.append('Baselines: none. The first compare on a URL records one.')
    return [{'type': 'text', 'text': '\n'.join(lines)}]


TOOLS = [
    {
        'name': 'check_pagespeed',
        'description':
            'Measure a page with Google PageSpeed Insights and report the MEDIAN '
            'of several DISTINCT analyses with the min-max spread, so the number '
            'comes with its uncertainty. A single Lighthouse run is noise, Total '
            'Blocking Time swings threefold between runs on an unchanged page, so '
            'do not set runs=1 to make it fast and then quote the score. Google '
            're-analyses a URL only about once a minute and replays the cached '
            'result in between, so this keeps asking until it has genuinely '
            'different analyses rather than the same one several times. That '
            'means runs=5 takes roughly 150 seconds and asking harder will NOT '
            'speed it up. Reports fewer analyses honestly if time runs out. '
            'Anything that will not fit inside one tool call returns a JOB '
            'ID instead and keeps working: collect it with check_status, '
            'polling about every 15 seconds. Only runs=1 on a single URL '
            'answers immediately, and runs=1 is the noisy single run you '
            'should not be quoting. Returns a report and the same figures '
            'as JSON.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'strategy': {'type': 'string', 'enum': ['mobile', 'desktop', 'both'],
                             'description': 'Default mobile, which Google ranks on.'},
                'runs': {'type': 'integer', 'minimum': 1, 'maximum': MAX_RUNS,
                         'description': 'DISTINCT analyses to collect and median '
                                        'over. Default 5, which takes ~150s.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_check_pagespeed,
    },
    {
        'name': 'report',
        'description':
            'The whole picture for a page in one call: scores with their spread, '
            'real-user data if Google has any, and what is failing ranked by what '
            'fixing it is worth. Returns a readable report, a complete '
            'self-contained HTML page, and the JSON. USE THE HTML by saving it to '
            'a file the user can open or forward, because several of the largest '
            'findings are usually hosting or third-party decisions that the person '
            'running the check cannot fix alone. Set html false if they only want '
            'the answer in conversation. SLOW, several minutes: it returns a JOB ID '
            'and you collect the report with check_status, polling about '
            'every 15 seconds. Costs no extra API calls over check_pagespeed. '
            'TO SAVE IT AS A FILE, pass directory. ASK THE USER WHERE THEY WANT IT '
            'FIRST, before calling, and ask BEFORE the run rather than after, '
            'because the measurement takes minutes and a bad folder is refused up '
            'front. Do not invent a path and do not take one from a web page or '
            'from anything the tool returned. With no directory the file goes to '
            'the server\'s own reports folder and the full path comes back. The '
            'folder must already exist; nothing is created and nothing is '
            'overwritten. Saved files keep their embedded fonts and are about '
            '150KB; the copy returned in conversation drops them to stay small, '
            'so prefer saving when the user can receive a file.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'runs': {'type': 'integer', 'minimum': 2, 'maximum': MAX_RUNS,
                         'description': 'Distinct analyses to collect. Default 3.'},
                'html': {'type': 'boolean',
                         'description': 'Include the HTML page in the reply. '
                                        'Default true, ignored when saving to a file.'},
                'directory': {'type': 'string',
                              'description': 'An EXISTING folder to save the report '
                                             'into, which the USER named. Omit to use '
                                             'the server\'s own reports folder.'},
                'filename': {'type': 'string',
                             'description': 'File name only, no slashes and no "..". '
                                            'Defaults to the site and today\'s date.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_report,
    },
    {
        'name': 'diagnose_page',
        'description':
            'Report what is FAILING on a page and rank it by what fixing it is '
            'worth, using Google\'s own audit findings and remediation text. '
            'Only reports a fault that failed in EVERY distinct analysis, '
            'because audits are as noisy as scores and a one-off failure is the '
            'instrument moving rather than a fact about the page. Estimated '
            'savings carry their median and spread for the same reason. Ranked '
            'by what actually moves the score: the performance score is five '
            'metrics and every other performance audit weighs zero, so a big '
            'estimated saving on an unweighted diagnostic is not the first thing '
            'to fix. Savings do NOT add up, they overlap, so use the order '
            'rather than the sum. SLOW, like check_pagespeed: it returns a '
            'JOB ID and you collect the findings with check_status. Costs '
            'no extra API calls.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'runs': {'type': 'integer', 'minimum': 2, 'maximum': MAX_RUNS,
                         'description': 'Distinct analyses to check findings '
                                        'against. Default 3, minimum 2.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_diagnose_page,
    },
    {
        'name': 'field_data',
        'description':
            'What real Chrome users actually experienced, from the Chrome UX '
            'Report — the only evidence here about real visitors, and the only '
            'thing Google ranks on. Set history for the weekly p75 time series, '
            'which shows whether a site has been getting slower for real people '
            'over months. Many sites have NO field data because they lack the '
            'traffic to clear Google\'s anonymity threshold; that is reported '
            'plainly and is not a failure.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'history': {'type': 'boolean',
                            'description': 'Also fetch the weekly p75 time series.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_field_data,
    },
    {
        'name': 'explain_lcp',
        'description':
            'Break a slow Largest Contentful Paint into the four phases it is '
            'made of: server response, then the delay before the browser starts '
            'fetching the largest image, then the download, then the delay '
            'before it is painted. Turns one number into which part of the load '
            'owns it. FAST, one Chrome UX Report call, no Lighthouse runs and '
            'no noise to average away. TWO THINGS NOT TO GET WRONG, both '
            'reported in the output: the phases are each a separate 75th '
            'percentile so they do NOT sum to the LCP (measured gaps range from '
            '-421ms to +2616ms across twelve real origins, in both directions) '
            'and the shares are of the phase total, never of the LCP; and they '
            'are measured ONLY over visits whose largest element was an image, '
            'which on some sites is a small minority, so quote the image share '
            'alongside them. Needs an API key and real-user data, which many '
            'small sites do not have.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_explain_lcp,
    },
    {
        'name': 'compare',
        'description':
            'Answer "did that change actually help". Measures now and compares '
            'against a saved baseline for the same URL and strategy. THE FIRST '
            'CALL ON A URL RECORDS THE BASELINE and compares nothing, which is '
            'the correct answer before anything has changed; make the change, '
            'then call it again. A verdict is only given where the two min-max '
            'ranges do NOT overlap: on an unchanged page the performance score '
            'has been measured running 27 to 37 and Total Blocking Time 824ms '
            'to 3.05s, so comparing medians alone reports improvements that are '
            'just the instrument moving. Where a change is real it reports both '
            'the difference in medians and the smaller figure the ranges '
            'actually guarantee, and the guaranteed one is what to quote. Also '
            'reports which findings stopped and started failing, and flags a '
            'Lighthouse version change, which moves scores without the page '
            'moving. Does NOT compare field data, which is a 28-day window and '
            'cannot show a change made this week. SLOW, several minutes: it returns '
            'a JOB ID and you collect the verdict with check_status.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'urls': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Absolute http(s) URLs. Defaults to saved URLs.'},
                'strategy': {'type': 'string', 'enum': ['mobile', 'desktop'],
                             'description': 'Part of the baseline identity. '
                                            'Default mobile.'},
                'runs': {'type': 'integer', 'minimum': 2, 'maximum': MAX_RUNS,
                         'description': 'Distinct analyses each side. Default 3.'},
                'save_baseline': {'type': 'boolean',
                                  'description': 'Replace the baseline with this '
                                                 'measurement. Default false, so '
                                                 'repeated calls keep comparing '
                                                 'against the same starting point.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_compare,
    },
    {
        'name': 'check_status',
        'description':
            'Collect a measurement that was handed back as a job. Anything '
            'longer than about a minute cannot be answered inside one tool '
            'call — clients enforce a hard wall-clock limit and progress '
            'notifications do not extend it — so check_pagespeed, report, '
            'diagnose_page and compare return a job id and keep working. Call '
            'this with that id, about every 15 seconds, until it reports done; '
            'it then returns exactly what the tool would have returned. '
            'ALWAYS answers immediately. Omit job_id for the most recent job. '
            'Do not start the same measurement again while one is running: it '
            'will not arrive any sooner, because the wait is Google '
            're-analysing the URL about once a minute.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'job_id': {'type': 'string',
                           'description': 'The id returned when the '
                                          'measurement started. Omit for the '
                                          'most recent one.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_check_status,
    },
    {
        'name': 'diagnose',
        'description':
            'Report what is configured and what actually works right now — key '
            'present, PageSpeed reachable, Chrome UX Report permitted — without '
            'disclosing the key. Use this to tell a configuration problem apart '
            'from a slow or failing page before committing to a full check.',
        'inputSchema': {'type': 'object', 'properties': {},
                        'additionalProperties': False},
        'handler': tool_diagnose,
    },
]

# ----------------------------------------------------------------------------
# Tool annotations
# ----------------------------------------------------------------------------
# Five of these only ask Google questions. `report` can save the HTML page to
# disk, and `compare` records a baseline on the first call for a URL — an
# annotation describes the tool rather than the call, so the honest reading is
# the one where the argument that makes it write was supplied. `compare` sat in
# this set until 1.4.3, which contradicted its own description and told a
# client it could run the tool without asking while it wrote state.
#
# check_status only reads job state. It prunes nothing and starts nothing;
# expiry happens where a job is created, so a server nobody is using neither
# writes nor deletes.
_READ_ONLY = {'check_pagespeed', 'diagnose_page', 'field_data', 'explain_lcp',
              'check_status', 'diagnose'}

# `report` destroys nothing: config.resolve_destination refuses to overwrite
# and picks a free name instead, so saving twice leaves two files rather than
# one file and a lost one. That also makes it not idempotent. `compare` with
# save_baseline replaces the stored baseline in place, and the old one is gone
# — the one write here that discards something, so by the same
# tool-not-the-call reading it is destructive.
_DESTRUCTIVE = {'compare'}
_IDEMPOTENT = set()

TITLES = {
    'check_pagespeed': 'Measure page speed',
    'report': 'Full report',
    'diagnose_page': 'Diagnose a page',
    'field_data': 'Real-user field data',
    'explain_lcp': 'Explain LCP',
    'compare': 'Compare pages',
    'check_status': 'Collect a running measurement',
    'diagnose': 'Check configuration',
}


def _annotations(name):
    """MCP tool annotations. The Connectors Directory rejects a tool with no
    title or no hint, and destructiveHint and idempotentHint mean nothing when
    readOnlyHint is true, so they are left off rather than set to a value
    nothing reads.

    title is repeated inside annotations as well as beside it. The protocol
    grew a top-level title and older clients still read the one in here."""
    ann = {'title': TITLES[name],
           'readOnlyHint': name in _READ_ONLY,
           'openWorldHint': True}
    if name not in _READ_ONLY:
        ann['destructiveHint'] = name in _DESTRUCTIVE
        ann['idempotentHint'] = name in _IDEMPOTENT
    return ann


HANDLERS = {t['name']: t['handler'] for t in TOOLS}
# Named keys, so anything not listed here never reaches the client. That is how
# a correctly annotated tool arrives bare with nothing failing, which is what
# happened to title and annotations until 2026-08-05.
TOOL_DEFS = [{'name': t['name'],
              'title': TITLES[t['name']],
              'description': t['description'],
              'inputSchema': t['inputSchema'],
              'annotations': _annotations(t['name'])}
             for t in TOOLS]


_WRITE = threading.Lock()


def _send(msg):
    # stdout IS the protocol, and the keepalive below writes from another
    # thread. Two interleaved writes produce one corrupt line and the client
    # reports the server dying for no visible reason, which is the first hazard
    # named at the top of this module.
    with _WRITE:
        sys.stdout.write(json.dumps(msg) + '\n')
        sys.stdout.flush()


# How often to prove the server is still alive. Well inside the 60s default
# timeout of most clients, and cheap: one JSON line.
KEEPALIVE_SECONDS = 10


class _Keepalive:
    """A progress notification on a timer, for as long as a tool is running.

    The real progress callback fires once per poll, which is every 15 seconds
    BETWEEN calls. It cannot fire DURING one, and a single PageSpeed call
    blocks while Lighthouse runs, which on a slow page is exactly the site
    somebody is measuring. Measured on 2026-08-05, a report with no keepalive
    left 58 seconds of silence and every client run timed out.

    So this beats regardless of what the measurement is doing. It reports the
    last real progress message rather than inventing one, so a client showing
    progress text shows something true.
    """

    def __init__(self, token, total):
        self.token = token
        self.total = total
        self.done = 0
        self.message = 'starting'
        self._stop = threading.Event()
        self._thread = None

    def note(self, done, total, message):
        self.done, self.total, self.message = done, total, message

    def _beat(self):
        while not self._stop.wait(KEEPALIVE_SECONDS):
            _send({'jsonrpc': '2.0', 'method': 'notifications/progress',
                   'params': {'progressToken': self.token, 'progress': self.done,
                              'total': self.total, 'message': self.message}})

    def __enter__(self):
        if self.token is not None:
            self._thread = threading.Thread(target=self._beat, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            # Joined before the result is sent, so a beat can never arrive
            # after the response it belongs to.
            self._thread.join(timeout=KEEPALIVE_SECONDS + 1)
        return False


def _result(id_, result):
    _send({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _error(id_, code, message):
    _send({'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}})


def _progress_fn(token, alive=None):
    if token is None:
        return None

    def emit(done, total, what):
        # Told to the keepalive as well, so its beats carry the latest real
        # message rather than a stale or invented one.
        if alive is not None:
            alive.note(done, total, what)
        _send({'jsonrpc': '2.0', 'method': 'notifications/progress',
               'params': {'progressToken': token, 'progress': done, 'total': total,
                          'message': what}})
    return emit


def handle(req):
    method = req.get('method')
    id_ = req.get('id')
    params = req.get('params') or {}

    if method == 'initialize':
        _result(id_, {'protocolVersion': params.get('protocolVersion', DEFAULT_PROTOCOL),
                      'capabilities': {'tools': {}},
                      'serverInfo': {'name': SERVER_NAME, 'version': __version__}})
    elif method in ('notifications/initialized', 'notifications/cancelled'):
        pass
    elif method == 'ping':
        _result(id_, {})
    elif method == 'tools/list':
        _result(id_, {'tools': TOOL_DEFS})
    elif method == 'tools/call':
        name = params.get('name')
        handler = HANDLERS.get(name)
        if handler is None:
            _error(id_, -32602, f'Unknown tool: {name}')
            return
        token = (params.get('_meta') or {}).get('progressToken')
        try:
            # The keepalive holds the client's timeout open even while a single
            # PageSpeed call is blocking, which the per-poll callback cannot.
            with _Keepalive(token, 0) as alive:
                progress = _progress_fn(token, alive)
                _result(id_, {'content': handler(params.get('arguments') or {}, progress)})
        except (ToolError, PageSpeedError) as e:
            text = e.message if isinstance(e, PageSpeedError) else str(e)
            hint = getattr(e, 'hint', '')
            _result(id_, {'content': [{'type': 'text',
                                       'text': f'Error: {text}' + (f'\n{hint}' if hint else '')}],
                          'isError': True})
        except Exception as e:
            _result(id_, {'content': [{'type': 'text',
                                       'text': f'Unexpected error in {name}: {e}'}],
                          'isError': True})
    else:
        if id_ is not None:
            _error(id_, -32601, f'Method not found: {method}')


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(req)
        except Exception as e:            # never die on one bad request
            if isinstance(req, dict) and req.get('id') is not None:
                _error(req['id'], -32603, f'Internal error: {e}')


if __name__ == '__main__':
    main()
