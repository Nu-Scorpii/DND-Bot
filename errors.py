"""Errors safe to show to the person who made a request."""


class RollError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RollError(message)
