import pytest

from jdssarrow.iem.transport_loopback import LoopbackTransport


@pytest.fixture(autouse=True)
def _reset_loopback():
    """Each test starts with a clean in-process loopback bus."""
    LoopbackTransport.reset()
    yield
    LoopbackTransport.reset()
