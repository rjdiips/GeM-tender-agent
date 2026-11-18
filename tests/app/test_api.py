import asyncio
import io
import json
import zipfile
from urllib.parse import unquote

import httpx
import pytest

from gem_agent.agents.rulings import RulingMatch
from gem_agent.app.main import create_app
from gem_agent.cache.docs import DocCache
from gem_agent.graph.manager import RunManager
from gem_agent.graph.store import Ruling
from tests.graph.fakes import (
    BlockingCollector,
    FakeChat,
    FakeCollector,
    FakeJudge,
    FakeRulings,
    FakeSearcher,
    bid,
)
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
    r = await client.post(f"/api/runs/{run_id}/restore/2")
    assert r.status_code == 200 and [b["b_id"] for b in r.json()["changed"]] == [2]
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


async def test_a_borderline_bid_enters_the_zip_only_when_ruled_counts(client, app_parts):
    await login(client)
    s = app_parts[0]
    run_id = (await ask(client, "LOS tenders"))[0]["run"]["run_id"]

    async def zipped():
        z = zipfile.ZipFile(io.BytesIO((await client.get(f"/api/runs/{run_id}/zip")).content))
        return any(n.startswith("GEM-2026-B-2/") for n in z.namelist())

    def make_borderline(all_):
        all_[2] = all_[2].model_copy(update={"verdict": all_[2].verdict.model_copy(update={"borderline": True})})
        return [2]

    def rule_counts(all_):
        ruling = Ruling(decision="counts", by="you", fact_id="f", fact="LOS counts", at=s.now())
        all_[2] = all_[2].model_copy(update={"ruling": ruling})
        return [2]

    s.store.update_outcomes(run_id, make_borderline)
    assert not await zipped()
    s.store.update_outcomes(run_id, rule_counts)
    assert await zipped()


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


# --- rulings ---------------------------------------------------------------------------------------------------

SAME = "Do core banking add-ons count?"


@pytest.fixture
def ruling_parts(tmp_path):
    """Bid 1 is relevant; bids 2 and 3 are borderline with the same question."""
    s = services(tmp_path, searcher=FakeSearcher([[bid(1), bid(2, "Core add-on", 18), bid(3, "Core module", 19)]]),
                 judge=FakeJudge(borderline={2, 3}, question=SAME), rulings=FakeRulings(match_first={SAME: "counts"}))
    return s, create_app(RunManager(s), memory=s.memory, cache=DocCache(tmp_path / "cache"), org="finsurge")


@pytest.fixture
async def rclient(ruling_parts):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ruling_parts[1]), base_url="http://t") as c:
        yield c


def rule_url(run_id, b_id):
    return f"/api/runs/{run_id}/rule/{b_id}"


async def ruled_run(client):
    await login(client)
    return (await ask(client, "LOS tenders"))[0]["run"]["run_id"]


async def kinds(client):
    return [f["kind"] for f in (await client.get("/api/memory")).json()]


async def states(client, run_id):
    return {b["b_id"]: (b["state"], (b["ruling"] or {}).get("by")) for b in (await client.get(f"/api/runs/{run_id}")).json()["results"]}


async def test_rule_counts_saves_a_fact_and_settles_matching_bids(rclient):
    run_id = await ruled_run(rclient)
    changed = (await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})).json()["changed"]
    assert {b["b_id"]: (b["state"], b["ruling"]["by"]) for b in changed} == {2: ("relevant", "you"), 3: ("relevant", "earlier")}
    assert (await kinds(rclient)).count("ruling") == 1


async def test_undo_of_a_click_removes_its_fact_and_unsettles(rclient, ruling_parts):
    s = ruling_parts[0]
    run_id = await ruled_run(rclient)
    await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})
    changed = (await rclient.delete(rule_url(run_id, 2))).json()["changed"]
    assert {b["b_id"] for b in changed} == {2, 3}
    assert await states(rclient, run_id) == {1: ("relevant", None), 2: ("borderline", None), 3: ("borderline", None)}
    assert "ruling" not in await kinds(rclient)
    assert s.store.outcomes(run_id)[2].undone and not s.store.outcomes(run_id)[3].undone
    assert (await rclient.delete(rule_url(run_id, 2))).json() == {"changed": []}


async def test_an_undone_bid_is_not_settled_again_by_a_later_ruling(rclient, ruling_parts):
    run_id = await ruled_run(rclient)
    await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})
    await rclient.delete(rule_url(run_id, 3))  # the user takes bid 3 back
    await rclient.delete(rule_url(run_id, 2))
    changed = (await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})).json()["changed"]
    assert [b["b_id"] for b in changed] == [2]
    assert (await states(rclient, run_id))[3] == ("borderline", None)


async def test_a_contradicting_ruling_replaces_the_old_fact(rclient, ruling_parts):
    s = ruling_parts[0]
    run_id = await ruled_run(rclient)
    old = s.memory.add_ruling("asha", "Add-ons count.", topic="Loan Origination System", run_id="r0", today=s.now().date(),
                              evidence="e")
    s.rulings.answers[SAME] = RulingMatch(fact_id=old.id, decision="counts")
    s.rulings.match_first = {}
    changed = (await rclient.post(rule_url(run_id, 2), json={"decision": "not"})).json()["changed"]
    assert [b["b_id"] for b in changed] == [2] and changed[0]["state"] == "quarantined"
    active = [f for f in (await rclient.get("/api/memory")).json() if f["kind"] == "ruling"]
    assert len(active) == 1 and active[0]["id"] != old.id and "(not)" in active[0]["fact"]
    assert next(f for f in s.memory.facts("asha") if f.id == old.id).evidence.startswith("replaced by your ruling on GEM/2026/B/2 on ")


async def test_a_matching_fact_with_the_same_decision_is_reused(rclient, ruling_parts):
    s = ruling_parts[0]
    run_id = await ruled_run(rclient)
    old = s.memory.add_ruling("asha", "Add-ons count.", topic="Loan Origination System", run_id="r0", today=s.now().date(),
                              evidence="e")
    s.rulings.answers[SAME] = RulingMatch(fact_id=old.id, decision="counts")
    s.rulings.match_first = {}
    changed = (await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})).json()["changed"]
    assert s.rulings.written == [] and (await kinds(rclient)).count("ruling") == 1
    assert changed[0]["ruling"]["fact"] == "Add-ons count."
    await rclient.delete(rule_url(run_id, 2))  # the fact was not created by this click, so it stays
    assert (await kinds(rclient)).count("ruling") == 1


async def test_restore_of_a_doesnt_count_bid_changes_the_ruling(rclient, ruling_parts):
    ruling_parts[0].rulings.match_first = {SAME: "not"}
    run_id = await ruled_run(rclient)
    await rclient.post(rule_url(run_id, 2), json={"decision": "not"})
    assert (await states(rclient, run_id))[2] == ("quarantined", "you")
    changed = (await rclient.post(f"/api/runs/{run_id}/restore/2")).json()["changed"]
    assert changed[0]["state"] == "relevant" and changed[0]["ruling"]["decision"] == "counts"
    assert (await kinds(rclient)).count("ruling") == 1


async def test_rule_on_a_bid_that_is_not_borderline_is_409(rclient, ruling_parts):
    s = ruling_parts[0]
    run_id = await ruled_run(rclient)
    before = s.store.outcomes(run_id)[1]
    assert (await rclient.post(rule_url(run_id, 1), json={"decision": "counts"})).status_code == 409
    assert s.store.outcomes(run_id)[1] == before and s.rulings.resolved == []


async def test_rule_and_undo_on_another_users_run_are_404(rclient):
    run_id = await ruled_run(rclient)
    await login(rclient, "ravi")
    assert (await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})).status_code == 404
    assert (await rclient.delete(rule_url(run_id, 2))).status_code == 404
    assert (await rclient.post(f"/api/runs/{run_id}/restore/2")).status_code == 404
    await login(rclient)
    assert (await rclient.post(rule_url(run_id, 99), json={"decision": "counts"})).status_code == 404


async def test_a_failing_fact_writer_is_502_and_changes_nothing(rclient, ruling_parts):
    s = ruling_parts[0]
    s.rulings.fail_write = True
    run_id = await ruled_run(rclient)
    before = s.store.outcomes(run_id)
    r = await rclient.post(rule_url(run_id, 2), json={"decision": "counts"})
    assert r.status_code == 502
    assert "ruling" not in await kinds(rclient) and s.store.outcomes(run_id) == before
    assert s.rulings.resolved == [SAME]  # only the match for the clicked bid; the sweep never ran


class BlockBid2(BlockingCollector):
    async def collect(self, bid, *, force=False, attachments=True):
        if bid.b_id != 2:
            return await FakeCollector.collect(self, bid, force=force, attachments=attachments)
        return await super().collect(bid, force=force, attachments=attachments)


async def test_ruling_while_the_run_is_going_loses_no_results(tmp_path):
    collector = BlockBid2()
    s = services(tmp_path, collector=collector, searcher=FakeSearcher([[bid(1, closing_day=18), bid(2, "Core add-on", 19)]]),
                 judge=FakeJudge(borderline={1, 2}, question=SAME), rulings=FakeRulings())
    manager = RunManager(s)

    async def drain():
        return [e async for e in manager.ask("asha", "LOS tenders")]

    stream = asyncio.create_task(drain())
    for _ in range(100):  # until bid 1 is saved while bid 2 is still blocked
        await asyncio.sleep(0.05)
        runs = s.store.list_runs("asha")
        if runs and 1 in s.store.outcomes(runs[0].run_id):
            break
    run_id = runs[0].run_id
    assert set(s.store.outcomes(run_id)) == {1}
    await manager.rule("asha", run_id, 1, "counts")
    collector.release.set()
    await stream
    outcomes = s.store.outcomes(run_id)
    assert set(outcomes) == {1, 2} and outcomes[1].ruling and outcomes[1].ruling.by == "you"
