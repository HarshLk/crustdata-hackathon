"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_broker_transports():
    """Transports registered by one test never leak into another."""
    from pdr import broker
    saved = dict(broker.TRANSPORTS)
    yield
    broker.TRANSPORTS.clear()
    broker.TRANSPORTS.update(saved)
