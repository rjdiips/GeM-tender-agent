from datetime import datetime

import pytest
from pydantic import ValidationError

from gem_agent.agents.planner import PLAN_INSTRUCTIONS, Planner, plan_output_model
from gem_agent.agents.schemas import DateWindow, TenderRequest
from gem_agent.tools.gem.lookups import Category
from tests.agents.helpers import LOS, ScriptedLLM

NOW = datetime(2026, 11, 16, 13, 40)
CATS = [Category(value="services_home_itse_hi12433188", label="Hiring Of Agency For It Projects- Milestone Basis"),
        Category(value="home_info_soft_loan_or", label="Loan Origination Software")]
MINISTRIES = ["Ministry of Finance", "FINANCE", "Ministry of Defence"]


class FakeLookups:
    def __init__(self):
        self.queries = []

    def find_categories(self, query, terms, k=15):
        self.queries.append((query, tuple(terms)))
        return CATS

    def ministries(self):
        return MINISTRIES

    def departments(self, hints, k=30):
        return ["Department of Financial Services"] if hints else []


def request(**kw):
    return TenderRequest(intent="search", topics=[LOS], window=DateWindow(kind="published", last_days=14).resolve(NOW),
                         user_text="LOS tenders", **kw)


GOOD = {"ministries": [], "departments": [], "categories": ["home_info_soft_loan_or"], "bid_types": [],
        "extra_terms": ["loan application processing"], "reasons": "category matches the topic"}


def test_output_schema_only_allows_site_values():
    Out = plan_output_model(MINISTRIES, ["Department of Financial Services"], [c.value for c in CATS])
    Out.model_validate(GOOD)
    with pytest.raises(ValidationError):
        Out.model_validate({**GOOD, "categories": ["software_invented_123"]})
    with pytest.raises(ValidationError):
        Out.model_validate({**GOOD, "ministries": ["Ministry of Banking"]})


async def test_plan_uses_real_values_and_keeps_the_window():
    lookups = FakeLookups()
    llm = ScriptedLLM({PLAN_INSTRUCTIONS: [GOOD]})
    plan = await Planner(llm.model(), lookups).plan(request(), LOS)
    assert plan.categories == ["home_info_soft_loan_or"]
    assert plan.published_from == datetime(2026, 11, 2) and plan.closing_from == NOW
    assert "LOS" in plan.terms and "loan application processing" in plan.terms
    assert plan.text.startswith(LOS.meaning)
    assert "Loan Origination Software (home_info_soft_loan_or)" in llm.calls[0][1]


async def test_invented_value_is_sent_back_and_corrected():
    bad = {**GOOD, "categories": ["software_invented_123"]}
    llm = ScriptedLLM({PLAN_INSTRUCTIONS: [bad, GOOD]})
    plan = await Planner(llm.model(), FakeLookups()).plan(request(), LOS)
    assert plan.categories == ["home_info_soft_loan_or"] and llm.count(PLAN_INSTRUCTIONS) == 2


async def test_buyer_hints_become_preferences_not_filters():
    llm = ScriptedLLM({PLAN_INSTRUCTIONS: [{**GOOD, "ministries": ["Ministry of Finance"],
                                            "departments": ["Department of Financial Services"]}]})
    plan = await Planner(llm.model(), FakeLookups()).plan(request(buyer_hints=["public sector banks"]), LOS)
    assert plan.departments == [] and plan.ministries == []
    assert plan.prefer_departments == ["Department of Financial Services"]
    assert plan.prefer_ministries == ["Ministry of Finance"]
    assert "public sector banks" in llm.calls[0][1]


async def test_buyer_only_makes_hard_filters():
    llm = ScriptedLLM({PLAN_INSTRUCTIONS: [{**GOOD, "departments": ["Department of Financial Services"]}]})
    req = request(buyer_hints=["public sector banks"], buyer_only=True)
    plan = await Planner(llm.model(), FakeLookups()).plan(req, LOS)
    assert plan.departments == ["Department of Financial Services"] and plan.prefer_departments == []


async def test_no_buyer_hints_offers_no_buyers():
    llm = ScriptedLLM({PLAN_INSTRUCTIONS: [GOOD]})
    plan = await Planner(llm.model(), FakeLookups()).plan(request(), LOS)
    assert "Ministries:" not in llm.calls[0][1]
    assert plan.ministries == [] and plan.prefer_ministries == []
