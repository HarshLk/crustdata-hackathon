"""Suite-wide fixtures."""
import os

import pytest

# No test may reach a real provider: real transports raise TransportOffline (-> PROVIDER_FAILED).
# Live smoke tests opt in with PDR_LIVE=1 and the `live` marker.
if os.environ.get("PDR_LIVE") != "1":
    os.environ["PDR_OFFLINE"] = "1"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: makes real provider calls; runs only with PDR_LIVE=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PDR_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live provider test: set PDR_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True, scope="session")
def _offline_session():
    """Keep PDR_OFFLINE=1 for the whole session unless PDR_LIVE=1."""
    if os.environ.get("PDR_LIVE") != "1":
        os.environ["PDR_OFFLINE"] = "1"
    yield


@pytest.fixture(autouse=True)
def _isolate_broker_transports():
    """Transports registered by one test never leak into another."""
    from pdr import broker
    saved = dict(broker.TRANSPORTS)
    yield
    broker.TRANSPORTS.clear()
    broker.TRANSPORTS.update(saved)
