"""Evaluation service (own container): scoring, storage and the dashboard.

Nothing in the Chainlit app imports this package — the app talks to it over HTTP
via ``evaluation.post_score``. Keeping the dependency edge one-way is what lets
the service own a heavy metric library without the app paying for it.
"""
