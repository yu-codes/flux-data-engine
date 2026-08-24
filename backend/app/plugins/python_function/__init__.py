"""Vetted Python transforms, and the Model provider that runs them.

Importing this package fills the registry: `library` registers the original
analytical transforms as a side effect of its own import, and `standard` adds
the general-purpose reshaping vocabulary that pipelines are composed from.
"""

from . import library, standard

standard.register_standard_transforms()

__all__ = ["library", "standard"]
