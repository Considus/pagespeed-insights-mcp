"""Honest PageSpeed Insights measurement, with a CLI and an MCP face.

Standard library only. No pip, no virtual environment, no compiled dependency —
`python3 -m pagespeed_insights` on any machine with Python 3.9 or newer.
"""
from .errors import (CredentialRejected, CruxUnavailable, PageSpeedError,
                     PageUnreachable, QuotaExhausted, Unavailable)

__version__ = '1.4.3'

__all__ = ['PageSpeedError', 'QuotaExhausted', 'CredentialRejected',
           'PageUnreachable', 'Unavailable', 'CruxUnavailable', '__version__']
