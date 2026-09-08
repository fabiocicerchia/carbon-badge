"""Entry point for `python -m carbon_badge`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
