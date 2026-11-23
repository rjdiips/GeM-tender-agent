"""The checkpoint store for runs, with our own types allowed by name.

LangGraph reloads a paused or crashed run's state from msgpack. By default it accepts any class with a warning
, so an
upgrade would leave every interrupted run unable to resume. Listing our types explicitly keeps resume working and
blocks anything else, which is also the safer setting: a class named in a checkpoint is imported and called.

The list is collected from RunState's own field types, so a field added later is covered without editing a list.
"""
from __future__ import annotations

import enum
import typing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from gem_agent.graph.state import RunState

OURS = "gem_agent."


def checkpoint_types(root: type[BaseModel] = RunState) -> set[type]:
    """Every model and enum of ours reachable from the state's fields, the state included."""
    found: set[type] = set()

    def visit(tp: object) -> None:
        for arg in typing.get_args(tp):
            visit(arg)
        if not isinstance(tp, type) or tp in found or not tp.__module__.startswith(OURS):
            return
        if issubclass(tp, BaseModel):
            found.add(tp)
            for field in tp.model_fields.values():
                visit(field.annotation)
        elif issubclass(tp, enum.Enum):
            found.add(tp)

    visit(root)
    return found


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=tuple(checkpoint_types()))


@asynccontextmanager
async def checkpointer(path: str | Path) -> AsyncIterator[AsyncSqliteSaver]:
    async with aiosqlite.connect(str(path)) as conn:
        yield AsyncSqliteSaver(conn, serde=serializer())
