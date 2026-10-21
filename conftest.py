"""Deterministic dice draws; every scripted value must be a real face."""

import pytest
from ua_bot.engine import DiceEngine
from ua_bot.storage import Store


class ScriptedRNG:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0
        self.domains = []

    def randint(self, low, high):
        self.calls += 1
        value = next(self.values)
        assert low <= value <= high
        return value

    def choice(self, faces):
        self.calls += 1
        self.domains.append(tuple(faces))
        value = next(self.values)
        assert value in faces
        return value


@pytest.fixture
def make_engine():
    stores = []
    def make(values=(), path=':memory:', owner=1):
        store = Store(path)
        stores.append(store)
        return DiceEngine(store, owner, ScriptedRNG(values))
    yield make
    for store in stores:
        try:
            store.close()
        except Exception:
            pass
