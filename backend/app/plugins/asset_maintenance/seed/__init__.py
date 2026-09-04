"""Everything the maintenance application puts in place on first run.

It lives here rather than in `app/core/` because the platform is not a
maintenance platform: adding or removing a built-in application must not mean
editing the core.
"""

from .worked_example import seed_maintenance_example

__all__ = ["seed_maintenance_example"]
