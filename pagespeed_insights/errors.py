"""Failures that carry their own remediation.

The script this package grew out of reported every failure by raising
SystemExit. That is fine in a script and wrong in a library, and it bit
immediately: SystemExit derives from BaseException, so `except Exception` does
not catch it, and the first MCP server built on that script would have been
killed mid-session by an exhausted quota instead of answering the call.

So nothing here raises SystemExit. Every failure is a PageSpeedError carrying a
`hint` — the sentence a person needs to fix it. The CLI prints the hint and
picks an exit code; the MCP server puts it in the tool result. Neither has to
know what went wrong to say something useful about it.
"""


class PageSpeedError(Exception):
    """Base. `hint` is remediation for a human, not a stack trace."""

    def __init__(self, message, hint=''):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return f'{self.message}\n{self.hint}' if self.hint else self.message


class QuotaExhausted(PageSpeedError):
    """HTTP 429. Distinct because the fix depends entirely on whether there is
    a key: keyless means the shared anonymous pool, which is routinely spent by
    other people and says nothing about the site being tested."""


class CredentialRejected(PageSpeedError):
    """HTTP 400/403 against PSI. The key is absent, wrong, or restricted in a
    way that makes it unusable from a script."""


class PageUnreachable(PageSpeedError):
    """Lighthouse could not load the page. Unlike everything else here, this one
    IS a fault on the tested site, and saying so plainly matters."""


class Unavailable(PageSpeedError):
    """The API could not be reached at all — network, DNS, timeout."""


class CruxUnavailable(PageSpeedError):
    """CrUX would not answer, and *why* decides what the caller should do.

    reason:
        'no_data'      404. The origin has too little traffic to clear Google's
                       anonymity threshold. Not an error. Expected for any small
                       or pre-launch site, and must never be reported as one.
        'restricted'   the key exists but is not permitted to call CrUX.
        'not_enabled'  the Chrome UX Report API is off for the project — OR the
                       enablement has not propagated yet, which is
                       indistinguishable from the outside. See crux.py.
    """

    def __init__(self, message, reason, hint='', console_url=''):
        super().__init__(message, hint)
        self.reason = reason
        self.console_url = console_url
