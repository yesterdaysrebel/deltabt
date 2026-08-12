"""Research track: pre-registered hypothesis tests.

Separate from `deltabt` proper because the goal differs. The backtester answers
"what would this strategy have returned"; this package answers "is there
credible evidence of an edge", which requires null models, correlation-adjusted
inference, and an append-only record of every hypothesis tried.
"""
