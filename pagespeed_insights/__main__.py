"""`python3 -m pagespeed_insights` runs the CLI."""
import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
