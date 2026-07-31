#!/usr/bin/env python3
"""Entry point for MCP clients.

    python3 /path/to/pagespeed-insights-mcp/mcp_server.py

A client launches its servers from whatever working directory it happens to be
in, so `-m pagespeed_insights.mcp` cannot be relied on — the package would have
to already be importable. This file puts its own directory on the path first,
which makes the client config a single absolute path with no cwd assumption and
nothing to set in the environment.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pagespeed_insights.mcp import main  # noqa: E402

if __name__ == '__main__':
    main()
