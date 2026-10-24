from instamagic.services.queues import snapshot_queues
from tests.conftest import login


def test_queues_endpoint_handles_redis_down(client, monkeypatch) -> None:
    login(client)

    def boom(_url: str):
        raise ConnectionError("down")

    monkeypatch.setattr(
        "instamagic.services.queues.snapshot_queues",
        lambda _url: snapshot_queues("redis://invalid"),
    )
    monkeypatch.setattr(
        "instamagic.services.queues.Redis.from_url",
        classmethod(lambda cls, _url: (_ for _ in ()).throw(ConnectionError("down"))),
    )

    # Direct unit path
    result = snapshot_queues("redis://localhost:1/0")
    assert result.available is False
    assert result.detail == "queue unavailable"
    assert {queue.name for queue in result.queues} == {"io", "ml", "gemini", "social"}

    # API path with patched snapshot
    monkeypatch.setattr(
        "instamagic.api.routes.snapshot_queues",
        lambda _url: result,
    )
    response = client.get("/api/queues")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["detail"] == "queue unavailable"


def test_queues_snapshot_shape(monkeypatch) -> None:
    class FakeRedis:
        @staticmethod
        def from_url(_url: str):
            return FakeRedis()

        def ping(self) -> bool:
            return True

    class FakeRegistry:
        count = 0

        def get_job_ids(self):
            return []

    class FakeQueue:
        def __init__(self, name, connection=None):
            self.name = name
            self.job_ids = []
            self.count = 0

    class FakeWorker:
        @staticmethod
        def all(**_kwargs):
            return [object()]

    monkeypatch.setattr("instamagic.services.queues.Redis", FakeRedis)
    monkeypatch.setattr("instamagic.services.queues.Queue", FakeQueue)
    monkeypatch.setattr(
        "instamagic.services.queues.StartedJobRegistry", lambda **_k: FakeRegistry()
    )
    monkeypatch.setattr("instamagic.services.queues.FailedJobRegistry", lambda **_k: FakeRegistry())
    monkeypatch.setattr("instamagic.services.queues.Worker", FakeWorker)

    result = snapshot_queues("redis://localhost/0")
    assert result.available is True
    assert result.queues[0].workers == 1
    assert result.queues[0].name == "io"
