"""advance-search (search-bids) and all-bids-data listing (spec 3.1)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from gem_agent.tools.gem.models import Bid
from gem_agent.tools.http_client import BidplusSession, is_no_data

PAGE_SIZE = 10  # fixed by the site; rows/limit are ignored

Sort = Literal["Bid-Start-Date-Latest", "Bid-Start-Date-Oldest", "Bid-End-Date-Latest", "Bid-End-Date-Oldest"]


def _day(value: date | None) -> str:
    return value.isoformat() if value else ""


class AdvancedQuery(Protocol):
    def payload(self, page: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CategorySearch:
    """Tab "Search by Bid / RA Details"."""

    category: str = ""
    bid_number: str = ""
    end_from: date | None = None
    end_to: date | None = None

    def payload(self, page: int) -> dict[str, Any]:
        return {"searchType": "bidNumber", "bidNumber": self.bid_number, "category": self.category,
                "bidEndFrom": _day(self.end_from), "bidEndTo": _day(self.end_to), "page": page}


@dataclass(frozen=True)
class MinistrySearch:
    """Tab "Search by Ministry / Organization". Needs a ministry or a buyer state."""

    ministry: str = ""
    buyer_state: str = ""
    organization: str = ""
    department: str = ""
    end_from: date | None = None
    end_to: date | None = None

    def payload(self, page: int) -> dict[str, Any]:
        return {"searchType": "ministry-search", "ministry": self.ministry, "buyerState": self.buyer_state,
                "organization": self.organization, "department": self.department,
                "bidEndFromMin": _day(self.end_from), "bidEndToMin": _day(self.end_to), "page": page}


@dataclass(frozen=True)
class ConsigneeSearch:
    """Tab "Search by Consignee Location"."""

    state: str
    city: str = ""
    end_from: date | None = None
    end_to: date | None = None

    def payload(self, page: int) -> dict[str, Any]:
        return {"searchType": "con", "state_name_con": self.state, "city_name_con": self.city,
                "bidEndFromCon": _day(self.end_from), "bidEndToCon": _day(self.end_to), "page": page}


@dataclass(frozen=True)
class BoqSearch:
    """Tab "Search by BOQ Title"."""

    boq_title: str
    end_from: date | None = None
    end_to: date | None = None

    def payload(self, page: int) -> dict[str, Any]:
        return {"searchType": "boq", "boqtitle_con": self.boq_title, "bidvalue": "",
                "bidEndFromBoq": _day(self.end_from), "bidEndToBoq": _day(self.end_to), "page": page}


@dataclass(frozen=True)
class AllBidsQuery:
    """The all-bids list. `sort="Bid-Start-Date-Latest"` gives newest-published first."""

    search_text: str = ""
    search_type: Literal["fullText", "exact"] = "fullText"
    by_type: str = "all"
    high_bid_value: str = ""
    end_from: date | None = None
    end_to: date | None = None
    sort: Sort = "Bid-Start-Date-Latest"

    def payload(self, page: int) -> dict[str, Any]:
        return {
            "page": page,
            "param": {"searchBid": self.search_text, "searchType": self.search_type},
            "filter": {"bidStatusType": "ongoing_bids", "byType": self.by_type, "highBidValue": self.high_bid_value,
                       "byEndDate": {"from": _day(self.end_from), "to": _day(self.end_to)}, "sort": self.sort},
        }


class SearchPage(BaseModel):
    num_found: int
    start: int
    bids: list[Bid]
    empty_confirmed: bool = False  # GeM itself said "No data found"

    @property
    def page_count(self) -> int:
        return math.ceil(self.num_found / PAGE_SIZE)


class SearchResult(BaseModel):
    num_found: int
    bids: list[Bid]
    pages_fetched: int
    complete: bool
    empty_confirmed: bool = False


class PageCountMismatch(Exception):
    """A page carried a different number of bids than numFound implies."""


def parse_page(resp: httpx.Response) -> SearchPage:
    if is_no_data(resp):
        return SearchPage(num_found=0, start=0, bids=[], empty_confirmed=True)
    resp.raise_for_status()
    body = resp.json()["response"]["response"]
    return SearchPage(num_found=int(body["numFound"]), start=int(body["start"]),
                      bids=[Bid.from_listing(d) for d in body["docs"]])


async def search_bids(session: BidplusSession, query: AdvancedQuery, page: int = 1) -> SearchPage:
    resp = await session.post_form("search-bids", {"payload": json.dumps(query.payload(page))})
    return parse_page(resp)


async def list_all_bids(session: BidplusSession, query: AllBidsQuery, page: int = 1) -> SearchPage:
    resp = await session.post_form("all-bids-data", {"payload": json.dumps(query.payload(page))}, referer="/all-bids")
    return parse_page(resp)


def _check_page(page: SearchPage) -> None:
    expected = min(PAGE_SIZE, page.num_found - page.start)
    if len(page.bids) != expected:
        raise PageCountMismatch(f"start={page.start}: {len(page.bids)} bids, numFound={page.num_found} implies {expected}")


async def search_bids_all(session: BidplusSession, query: AdvancedQuery, max_pages: int | None = None) -> SearchResult:
    """Every page of an advanced search (R3). Bids repeated across pages, as the listing shifts, are kept once."""
    first = await search_bids(session, query, 1)
    if first.empty_confirmed:
        return SearchResult(num_found=0, bids=[], pages_fetched=1, complete=True, empty_confirmed=True)
    _check_page(first)
    last = first.page_count if max_pages is None else min(first.page_count, max_pages)
    seen = {b.b_id: b for b in first.bids}
    for n in range(2, last + 1):
        page = await search_bids(session, query, n)
        _check_page(page)
        for bid in page.bids:
            seen.setdefault(bid.b_id, bid)
    return SearchResult(num_found=first.num_found, bids=list(seen.values()), pages_fetched=max(last, 1),
                        complete=last >= first.page_count)
