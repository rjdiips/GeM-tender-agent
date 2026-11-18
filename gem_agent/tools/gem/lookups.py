"""Ministries, orgs, departments, states, cities, BOQ titles, categories (spec 3.1, R2)."""
from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel

from gem_agent.tools.http_client import TOKEN_PAGE, BidplusSession, is_no_data

NULLISH = {"", "null", "na", "n/a"}


class LookupFailed(Exception):
    """A lookup list came back without its success flag."""


class Category(BaseModel):
    value: str  # the exact site value, e.g. services_home_itse_hi12433188
    label: str


class BoqTitle(BaseModel):
    title: str
    key: str


def _clean(values: list[str]) -> list[str]:
    """Drop the empty and null-like entries the site itself hides."""
    return [v for v in values if isinstance(v, str) and v.strip().lower() not in NULLISH]


async def _post_json(session: BidplusSession, path: str, data: dict[str, str] | None = None) -> Any:
    resp = await session.post_form(path, data or {})
    if is_no_data(resp):
        return None
    resp.raise_for_status()
    return json.loads(resp.text) if resp.text.strip() else None


def _data(body: Any, path: str) -> Any:
    if not isinstance(body, dict) or body.get("status") != 200:
        raise LookupFailed(f"{path}: {str(body)[:200]}")
    return body.get("data")


async def list_ministries(session: BidplusSession) -> list[str]:
    """All 77 entries, duplicate spellings included: empty ones only cost a "No data found" (spec 5.2)."""
    return _clean(_data(await _post_json(session, "ministry-list-adv"), "ministry-list-adv")["MinistryList"])


async def list_buyer_states(session: BidplusSession) -> list[str]:
    return _clean(_data(await _post_json(session, "ministry-list-adv"), "ministry-list-adv")["BuyerStateList"])


async def list_organizations(session: BidplusSession, *, ministry: str = "", buyer_state: str = "") -> list[str]:
    if bool(ministry) == bool(buyer_state):
        raise ValueError("give exactly one of ministry or buyer_state")
    data = {"ministry": ministry} if ministry else {"buyer_state": buyer_state}
    return _clean(await _post_json(session, "org-list-adv", data) or [])


async def list_departments(
    session: BidplusSession, *, organization: str, ministry: str = "", buyer_state: str = ""
) -> list[str]:
    data = {"ministry": ministry, "buyer_state": buyer_state, "organization": organization}
    return _clean(await _post_json(session, "dept-list-adv", data) or [])


async def list_states(session: BidplusSession) -> list[str]:
    """Consignee states."""
    return _clean([d.get("state_name") for d in _data(await _post_json(session, "state-list-adv"), "state-list-adv")])


async def list_cities(session: BidplusSession, state: str) -> list[str]:
    body = await _post_json(session, "city-list-adv", {"state_name": state})
    if not isinstance(body, dict) or body.get("success") != 1:
        raise LookupFailed(f"city-list-adv: {str(body)[:200]}")
    return _clean([d.get("city_name") for d in body["data"]])


async def list_boq_titles(session: BidplusSession) -> list[BoqTitle]:
    rows = _data(await _post_json(session, "boq-title-adv"), "boq-title-adv")
    return [BoqTitle(title=r["bbt_title"], key=str(r["bbt_key"])) for r in rows if r.get("bbt_title")]


class _CategoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_select = False
        self.value: str | None = None
        self.text: list[str] = []
        self.options: list[Category] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "select" and a.get("id") == "categorybid":
            self.in_select = True
        elif tag == "option" and self.in_select:
            self._flush()
            self.value, self.text = a.get("value") or "", []

    def handle_data(self, data: str) -> None:
        if self.value is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._flush()
        elif tag == "select" and self.in_select:
            self._flush()
            self.in_select = False

    def _flush(self) -> None:
        if self.value:
            self.options.append(Category(value=self.value, label="".join(self.text).strip()))
        self.value = None


def parse_categories(html: str) -> list[Category]:
    parser = _CategoryParser()
    parser.feed(html)
    return parser.options


async def list_categories(session: BidplusSession) -> list[Category]:
    """The ~14k categories from the advance-search page's <select id="categorybid">."""
    page = await session.get(TOKEN_PAGE)
    page.raise_for_status()
    return parse_categories(page.text)
