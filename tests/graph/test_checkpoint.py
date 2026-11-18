"""Checkpoints reload our own types by name and nothing else (17 Nov): LangGraph accepts unlisted classes with a
warning that says they will be blocked in a future version, which would leave interrupted runs unable to resume."""
import logging
from datetime import datetime

from pydantic import BaseModel

from gem_agent.agents.schemas import SearchPlan, TenderRequest
from gem_agent.agents.searcher import Candidate
from gem_agent.graph.checkpoint import checkpoint_types, serializer
from gem_agent.graph.state import RunState
from gem_agent.tools.gem.models import BidType
from tests.agents.helpers import LOS
from tests.graph.fakes import bid


def full_state() -> RunState:
    return RunState(run_id="r1", user="raj", request=TenderRequest(intent="search", user_text="LOS", topics=[LOS]),
                    plans=[SearchPlan(topic_key=LOS.key(), text="loan origination", terms=["LOS"],
                                      published_from=datetime(2026, 11, 3))],
                    candidates=[Candidate(bid=bid(7), group="LOS", why=["title"])], done_ids=[7])


def test_every_type_the_log_warned_about_is_listed():
    assert {TenderRequest, SearchPlan, BidType, Candidate, RunState} <= checkpoint_types()


def test_a_run_state_reloads_whole_without_a_warning(caplog):
    serde = serializer()
    state = full_state()
    with caplog.at_level(logging.WARNING):
        back = serde.loads_typed(serde.dumps_typed(state))
    assert back == state
    assert not [r for r in caplog.records if "unregistered" in r.getMessage()]


class Stranger(BaseModel):
    x: int = 1


def test_a_class_that_is_not_ours_is_not_rebuilt_from_a_checkpoint():
    serde = serializer()
    assert not isinstance(serde.loads_typed(serde.dumps_typed(Stranger())), Stranger)
