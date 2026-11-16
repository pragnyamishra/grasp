"""Grasp: a reviewer's assistant for pull requests.

Grasp helps a human understand and triage a change before it is merged.
It reads a diff, scores the risk of each file with deterministic rules,
and, when a model is available, adds a plain explanation and the points
a reviewer should check. It never runs the code it reads, and it can run
fully offline.
"""

__version__ = "0.1.0"
