import json
import os

from gem_agent.memory.topics import TopicStore
from tests.agents.helpers import LOS

AML = LOS.model_copy(update={"meaning": "Anti-Money Laundering (AML) software for banks"})


def test_saved_meaning_found_by_normalised_text(tmp_path):
    s = TopicStore(tmp_path)
    s.save("asha", "LOS tenders", [LOS])
    assert s.get("asha", "  los   Tenders ") == [LOS]
    assert s.get("ravi", "LOS tenders") == []


def test_correction_replaces_saved_meaning(tmp_path):
    s = TopicStore(tmp_path)
    s.save("asha", "LOS", [LOS])
    other = LOS.model_copy(update={"meaning": "Line of Sight radio links"})
    s.save("asha", "LOS", [other])
    assert [t.meaning for t in s.get("asha", "LOS")] == ["Line of Sight radio links"]


def test_a_request_with_two_meanings_keeps_both(tmp_path):
    s = TopicStore(tmp_path)
    s.save("asha", "LOS and AML tenders", [LOS, AML])
    assert s.get("asha", "LOS and AML tenders") == [LOS, AML]
    assert [t.meaning for _, t in s.all("asha")] == [LOS.meaning, AML.meaning]


def test_all_saved_meanings_for_a_user_most_recent_first_and_capped(tmp_path):
    s = TopicStore(tmp_path)
    for i in range(5):
        s.save("asha", f"request {i}", [LOS])
        path = s._path("asha", f"request {i}")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    assert [text for text, _ in s.all("asha", limit=3)] == ["request 4", "request 3", "request 2"]
    assert len(s.all("asha")) == 5


def test_a_meaning_saved_in_the_old_single_topic_format_still_reads(tmp_path):
    s = TopicStore(tmp_path)
    path = s._path("asha", "LOS tenders")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"text": "los tenders", "topic": LOS.model_dump()}))
    assert s.get("asha", "LOS tenders") == [LOS]
