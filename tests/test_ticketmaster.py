import threading
import time

import httpx
import pytest

from gigmap import ticketmaster


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")
    monkeypatch.setattr(ticketmaster, "DELAY", 0.2)
    monkeypatch.setattr(ticketmaster, "_last_call", 0.0)


def test_threads_stay_one_delay_apart(monkeypatch):
    times = []
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: times.append(time.monotonic())
        or httpx.Response(200, json={}, request=httpx.Request("GET", "http://x")),
    )
    threads = [threading.Thread(target=ticketmaster._get, args=({},)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(times) == 2
    assert times[1] - times[0] >= ticketmaster.DELAY - 0.01


def test_429_raises_and_keeps_no_page_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGMAP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: httpx.Response(429, request=httpx.Request("GET", "http://x"))
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        ticketmaster._get({})
    assert not hasattr(ticketmaster, "CACHE_DIR")
    assert list(tmp_path.iterdir()) == []
