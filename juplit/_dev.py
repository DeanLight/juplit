"""Internal dev utilities for the juplit package itself."""

import os
import sys


def check_env(env_name: str) -> None:
    """Assert that an environment variable is set; exit with an error if not.

    Used as a poe task guard before publishing to PyPI.
    """
    if not os.environ.get(env_name):
        print(f"Error: environment variable {env_name!r} is not set.", file=sys.stderr)
        print(f"  Set it with: export {env_name}=<your-token>", file=sys.stderr)
        raise SystemExit(1)
