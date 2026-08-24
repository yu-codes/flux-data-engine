"""Reporting: documents assembled from what the platform has produced.

A report references results, executions, models and datasets and renders
them to Markdown, HTML or JSON. It reads across the stack, so it sits at
the composition level beside dashboards and pipelines rather than inside
the module that owns any one of the things it references.
"""
