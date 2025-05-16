import pytest


class DummyServer:
    def __init__(self):
        self.app = "dummy_app"


@pytest.fixture
def server():
    server = DummyServer()
    yield server


@pytest.mark.asyncio
async def test_server_fixture(server):
    assert server.app == "dummy_app"
