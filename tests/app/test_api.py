import io
import json
import zipfile
from urllib.parse import unquote

import httpx
import pytest

from gem_agent.app.main import create_app
from gem_agent.cache.docs import DocCache
from gem_agent.graph.manager import RunManager
from tests.graph.fakes import FakeChat, FakeJudge
from tests.graph.test_run_graph import services


@pytest.fixture
def app_parts(tmp_path):
    s = services(tmp_path, judge=FakeJudge(no={2}))
    cache = DocCache(tmp_path / "cache")
    for b in (1, 2):  # the fake collector's file paths
        for name in ("a.pdf", "b.pdf"):
            path = cache.root / f"docs/{b}/files/{name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%%PDF-1.4 bid %d" % b)
    s.memory.add_stated("asha", "We sell LOS software", run_id="r0", today=s.now().date())
    return s, create_app(RunManager(s), memory=s.memory, cache=cache, org="finsurge")


@pytest.fixture
async def client(app_parts):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_parts[1]), base_url="http://t") as c:
        yield c


async def login(client, name="asha"):
    r = await client.post("/api/login", json={"name": name})
    assert r.status_code == 200


def sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def ask(client, text, run_id=None):
    r = await client.post("/api/ask", json={"text": text, "run_id": run_id})
    assert r.headers["content-type"].startswith("text/event-stream")
    return sse(r.text)


async def test_needs_sign_in(client):
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.get("/api/runs")).status_code == 401


@pytest.mark.parametrize("name", [".", "..", "...."])
async def test_dot_only_names_are_refused(client, name):
    assert (await client.post("/api/login", json={"name": name})).status_code == 422
    client.cookies.set("gem_user", name)
    assert (await client.get("/api/me")).status_code == 401


async def test_sign_in_and_me(client):
    await login(client)
    assert (await client.get("/api/me")).json() == {"name": "asha", "org": "finsurge"}


async def test_ask_streams_events_and_the_run_is_listed(client):
    await login(client)
    events = await ask(client, "LOS tenders")
    assert events[0]["type"] == "started" and events[-1]["type"] == "done"
    run_id = events[0]["run"]["run_id"]
    runs = (await client.get("/api/runs")).json()
    assert runs[0]["run_id"] == run_id and runs[0]["found"] == 1
    view = (await client.get(f"/api/runs/{run_id}")).json()
    assert len(view["results"]) == 2 and view["status"] == "complete"


async def test_runs_are_private(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    await login(client, "ravi")
    assert (await client.get("/api/runs")).json() == []
    assert (await client.get(f"/api/runs/{run_id}")).status_code == 404


async def test_events_replay_a_finished_run(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    replay = sse((await client.get(f"/api/runs/{run_id}/events")).text)
    assert [e["type"] for e in replay][:2] == ["started", "understood"] and replay[-1]["type"] == "done"


async def test_restore(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    assert (await client.post(f"/api/runs/{run_id}/restore/2")).status_code == 204
    view = (await client.get(f"/api/runs/{run_id}")).json()
    assert next(b for b in view["results"] if b["b_id"] == 2)["restored"]


async def test_file_download_and_zip(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    view = (await client.get(f"/api/runs/{run_id}")).json()
    rfp = next(f for b in view["results"] if b["b_id"] == 1 for f in b["files"] if f["name"] == "RFP.pdf")
    r = await client.get(rfp["url"])
    assert r.status_code == 200 and r.content == b"%PDF-1.4 bid 1"
    # the bid and what it is; spaces make Starlette send the RFC 5987 form, which browsers decode
    assert unquote(r.headers["content-disposition"]).endswith("utf-8''GEM-2026-B-1 - RFP.pdf")
    z = zipfile.ZipFile(io.BytesIO((await client.get(view["zip_url"])).content))
    assert sorted(z.namelist()) == ["GEM-2026-B-1/GEM-2026-B-1 - Bid document.pdf",
                                    "GEM-2026-B-1/GEM-2026-B-1 - RFP.pdf"]  # relevant bids only


async def test_files_of_other_users_runs_are_refused(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    url = f"/api/runs/{run_id}/files/1/rfp1"
    await login(client, "ravi")
    assert (await client.get(url)).status_code == 404


async def test_unknown_file_is_404(client):
    await login(client)
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]
    assert (await client.get(f"/api/runs/{run_id}/files/1/nope")).status_code == 404


async def test_memory_list_edit_forget(client):
    await login(client)
    [fact] = (await client.get("/api/memory")).json()
    assert fact["fact"] == "We sell LOS software" and fact["source"] == "stated"
    assert fact["kind"] == "general" and fact["topic"] == ""
    assert (await client.patch(f"/api/memory/{fact['id']}", json={"text": "We sell LOS and AML software"})).status_code == 204
    assert (await client.get("/api/memory")).json()[0]["fact"] == "We sell LOS and AML software"
    assert (await client.delete(f"/api/memory/{fact['id']}")).status_code == 204
    assert (await client.get("/api/memory")).json() == []


async def test_answer_without_a_run(app_parts, client):
    s, _ = app_parts
    s.chat = FakeChat(intent="memory_list", topics=[])
    await login(client)
    events = await ask(client, "what do you remember?")
    assert events[0] == {"type": "show", "tab": "memory"} and events[1]["type"] == "message"


async def test_stop_route(app_parts, client):
    from tests.graph.fakes import BlockingCollector

    s, _ = app_parts
    s.collector = BlockingCollector()
    await login(client)
    import asyncio
    task = asyncio.create_task(ask(client, "LOS tenders"))
    while not s.store.list_runs("asha") or s.store.list_runs("asha")[0].status != "running":
        await asyncio.sleep(0.02)
    run_id = s.store.list_runs("asha")[0].run_id
    assert (await client.post(f"/api/runs/{run_id}/stop")).status_code == 204
    events = await asyncio.wait_for(task, timeout=5)
    assert events[-1] == {"type": "done", "status": "stopped"}
    assert (await client.post(f"/api/runs/{run_id}/stop")).status_code == 409  # already stopped
    await login(client, "ravi")
    assert (await client.post(f"/api/runs/{run_id}/stop")).status_code == 404
