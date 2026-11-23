"""Typed records parsed from GeM listing rows."""
from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class BidType(IntEnum):
    BID = 1
    RA = 2  # reverse auction
    DIRECT_RA = 5


def _first(doc: dict[str, Any], key: str) -> Any:
    """Listing fields come as one-item lists (Solr style); some are absent."""
    value = doc.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


SITE_TZ = ZoneInfo("Asia/Kolkata")


def site_now() -> datetime:
    """Current IST clock time, naive, comparable with GeM's timestamps."""
    return datetime.now(SITE_TZ).replace(tzinfo=None)


def parse_site_datetime(value: str | None) -> datetime | None:
    """GeM's "…Z" timestamps are IST wall-clock time: the site itself renders them with timeZone 'UTC'."""
    if not value:
        return None
    return datetime.fromisoformat(value.rstrip("Z"))


class Bid(BaseModel):
    b_id: int
    bid_number: str
    title: str
    category_id: str | None = None
    ministry: str | None = None
    department: str | None = None
    published: datetime | None = None
    closing: datetime | None = None
    bid_type: BidType | int
    eval_type: int = 0
    parent_b_id: int | None = None
    parent_bid_number: str | None = None
    total_quantity: int | None = None
    is_high_value: bool = False
    status: int | None = None
    raw: dict[str, Any] = {}

    @classmethod
    def from_listing(cls, doc: dict[str, Any]) -> Bid:
        bid_type = int(_first(doc, "b_bid_type") or 0)
        return cls(
            b_id=int(_first(doc, "b_id")),
            bid_number=_first(doc, "b_bid_number"),
            title=_first(doc, "b_category_name") or _first(doc, "bd_category_name") or "",
            category_id=_first(doc, "b_cat_id"),
            ministry=_first(doc, "ba_official_details_minName"),
            department=_first(doc, "ba_official_details_deptName"),
            published=parse_site_datetime(_first(doc, "final_start_date_sort")),
            closing=parse_site_datetime(_first(doc, "final_end_date_sort")),
            bid_type=BidType(bid_type) if bid_type in BidType._value2member_map_ else bid_type,
            eval_type=int(_first(doc, "b_eval_type") or 0),
            parent_b_id=_first(doc, "b_id_parent"),
            parent_bid_number=_first(doc, "b_bid_number_parent"),
            total_quantity=_first(doc, "b_total_quantity"),
            is_high_value=bool(_first(doc, "is_high_value")),
            status=_first(doc, "b_status"),
            raw=doc,
        )
