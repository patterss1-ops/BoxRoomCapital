"""Shared test fixtures and fakes."""
from __future__ import annotations


class FakeWriteEventStore:
    """EventStore that records write_event calls for assertion."""

    def __init__(self, *args, **kwargs):
        self.events: list = []

    def write_event(self, event):
        self.events.append(event)
        return {"id": f"evt-{len(self.events)}"}


class FakeFeatureStore:
    """FeatureStore stub that accepts but ignores all calls."""

    def __init__(self, *args, **kwargs):
        pass

    def close(self):
        pass
