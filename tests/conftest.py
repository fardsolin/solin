from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests


@pytest.fixture(autouse=True)
def no_exchange_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network access forbidden in the acceptance suite")

    monkeypatch.setattr(requests.sessions.Session, "request", deny)
