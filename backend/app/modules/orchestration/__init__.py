"""Orchestration: running models in order.

A pipeline is a graph of model executions over datasets. It belongs here
rather than in `data` because it depends on execution and results, and a
module about ingesting bytes must not depend on a module about running
models - that is the dependency direction the architecture states.
"""
