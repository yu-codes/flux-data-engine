"""Evaluation: comparing models and recording what they scored.

An Experiment says what is being compared and on what data; running it
submits one execution per trial; an Evaluation records a judgement about a
run against a stated target. All three read executions and results, which
is why they sit above the module that merely defines models.
"""
