"""Jobs: work that takes longer than a request should.

Four things in this platform take an unbounded amount of time - an execution, a
pipeline run, an experiment run, and a report export - and only the first had
anywhere to go but the request thread. A twelve-step pipeline ran inside the
HTTP call that asked for it, which is a request timeout waiting to happen and a
page that appears to hang while it works.

A Job is the record of one such piece of work: what kind, what it is working
on, whether it is still going, and what it produced. The module knows none of
those kinds. Handlers are supplied by the composition root, which is what lets
this sit below everything it runs rather than above it.
"""
