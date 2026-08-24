"""Everything the typhoon application puts in place on first run.

It lives here rather than in `app/core/` because the platform is not a typhoon
platform: adding or removing a built-in application must not mean editing the
core, and a core that imports `seed_typhoon` by name has already failed that.
"""

from .backtests import seed_typhoon_example

__all__ = ["seed_typhoon_example"]
