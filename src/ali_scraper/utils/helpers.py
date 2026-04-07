"""Shared helper functions."""

import random
from ..config.settings import settings


def pick_ua() -> str:
    """Return a random user-agent string."""
    return random.choice(settings.USER_AGENTS)
