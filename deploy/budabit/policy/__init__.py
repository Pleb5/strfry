"""Write-policy stages for the Budabit strfry deployment.

The entrypoint ``write-policy.py`` composes the stages in this package into a
pipeline (see ``pipeline.py``). Stages are ordered: storage guard, Budabit
community write control, then rate limiting. The first stage that rejects an
event wins; accepted events are committed to every stage so that rate-limit
tokens are consumed and Budabit community state is updated only for writes
that the whole pipeline accepted.
"""
