import os

import pytest

from jdssarrow.iem.transport_loopback import LoopbackTransport

# The web app now gates /api behind a login. Existing API tests predate that, so bypass the gate
# by default; tests that exercise auth explicitly set/clear JDSS_AUTH_DISABLED themselves.
os.environ.setdefault("JDSS_AUTH_DISABLED", "1")


@pytest.fixture(autouse=True)
def _reset_loopback():
    """Each test starts with a clean in-process loopback bus."""
    LoopbackTransport.reset()
    yield
    LoopbackTransport.reset()
