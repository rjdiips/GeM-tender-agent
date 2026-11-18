import pytest

from gem_agent.tools.gem.lookups import (
    list_boq_titles,
    list_buyer_states,
    list_categories,
    list_cities,
    list_departments,
    list_ministries,
    list_organizations,
    list_states,
)
from gem_agent.tools.http_client import BidplusSession, ClientPool

B = "https://bidplus.gem.gov.in"


@pytest.fixture
def session(gem, clock):
    gem.add("GET", f"{B}/advance-search", "advance_search.html.gz")
    return BidplusSession(ClientPool(transport=gem.transport(), clock=clock, sleep=clock.sleep))


async def test_ministries_keep_every_spelling(gem, session):
    gem.add("POST", f"{B}/ministry-list-adv", "ministry_list.json")
    ministries = await list_ministries(session)
    assert len(ministries) == 77
    assert "FINANCE" in ministries and "Ministry of Finance" in ministries


async def test_buyer_states_come_from_ministry_list(gem, session):
    gem.add("POST", f"{B}/ministry-list-adv", "ministry_list.json")
    states = await list_buyer_states(session)
    assert len(states) == 38 and "ANDHRA  PRADESH" in states


async def test_organizations_drop_null_placeholders(gem, session):
    gem.add("POST", f"{B}/org-list-adv", "org_list_ministry.json")
    orgs = await list_organizations(session, ministry="Ministry of Finance")
    assert len(orgs) == 148 and "N/A" not in orgs
    assert gem.form(1)["ministry"] == "Ministry of Finance"


async def test_organizations_need_exactly_one_parent(session):
    with pytest.raises(ValueError):
        await list_organizations(session)


async def test_departments(gem, session):
    gem.add("POST", f"{B}/dept-list-adv", "dept_list.json")
    depts = await list_departments(session, organization="Additional Treasury Tankipora", ministry="Ministry of Finance")
    assert depts == ["Department of Accounts and Treasuries"]
    assert gem.form(1) | {"csrf_bd_gem_nk": ""} == {
        "ministry": "Ministry of Finance", "buyer_state": "", "organization": "Additional Treasury Tankipora",
        "csrf_bd_gem_nk": "",
    }


async def test_consignee_states_and_cities(gem, session):
    gem.add("POST", f"{B}/state-list-adv", "state_list.json")
    gem.add("POST", f"{B}/city-list-adv", "city_list.json")
    states = await list_states(session)
    assert states[0] == "ANDAMAN & NICOBAR"
    assert await list_cities(session, "ANDAMAN & NICOBAR") == [
        "ANDAMAN", "NICOBAR", "NORTH AND MIDDLE ANDAMAN", "SOUTH ANDAMAN"]


async def test_boq_titles(gem, session):
    gem.add("POST", f"{B}/boq-title-adv", "boq_title.json")
    titles = await list_boq_titles(session)
    assert titles[0].title == "Polypropylene Dryer Package" and titles[0].key == "967969284"


async def test_categories_are_exact_site_values(session):
    cats = await list_categories(session)
    assert len(cats) == 14286
    by_value = {c.value: c.label for c in cats}
    assert by_value["services_home_itse_hi12433188"] == "Hiring Of Agency For It Projects- Milestone Basis"
    assert "" not in by_value
