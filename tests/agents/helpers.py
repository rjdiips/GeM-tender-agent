"""Test-only: bid files built straight into a DocCache, a scripted stand-in for the model, a sample topic."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from gem_agent.agents.schemas import TopicDefinition
from gem_agent.cache.docs import BidFiles, DocCache, FileRecord
from gem_agent.tools.pdf.text import PageText

LOS = TopicDefinition(
    user_text="LOS tenders",
    meaning="Loan Origination System: software banks/NBFCs use to capture, assess, approve and disburse loan applications",
    domain="banking / lending IT",
    match_terms=["Loan Origination System", "LOS", "loan origination", "digital lending platform"],
    acronyms={"LOS": "Loan Origination System"},
    related_but_not_enough=["Loan Management System (LMS)", "core banking"],
    not_this=["LOST (lost/found)", "loss", "Line of Sight", "Level of Service"],
)


def bid_files(cache: DocCache, b_id: int, files: list[tuple[str, str, list[str]]]) -> BidFiles:
    """files: (filename, role, page texts)."""
    records = []
    for name, role, pages in files:
        sha, path = cache.store_file(b_id, f"{name}:{'|'.join(pages)}".encode(), ".pdf")
        text_path = cache.store_text(path, [PageText(page=i + 1, text=t, method="text") for i, t in enumerate(pages)])
        records.append(FileRecord(role=role, url=f"https://bidplus.gem.gov.in/{name}", filename=name,
                                  size=10, kind="pdf", status="ok", sha256=sha, path=path, text_path=text_path,
                                  pages=len(pages)))
    return BidFiles(b_id=b_id, bid_number=f"GEM/2026/B/{b_id}", fetched_at=datetime(2026, 11, 16),
                    corrigendum_flag=False, files=records)


class ScriptedLLM:
    """Answers each call by its instructions: `script[instructions]` is a list of replies (dicts), used in order."""

    def __init__(self, script: dict[str, list[dict] | Callable[[str], dict]]) -> None:
        self.script = {k: (list(v) if isinstance(v, list) else v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []  # (instructions, user prompt)

    def _reply(self, messages, info: AgentInfo) -> ModelResponse:
        instructions = messages[-1].instructions or ""
        prompt = "".join(str(p.content) for p in messages[-1].parts if hasattr(p, "content"))
        self.calls.append((instructions, prompt))
        replies = self.script[instructions]
        reply = replies(prompt) if callable(replies) else (replies.pop(0) if len(replies) > 1 else replies[0])
        return ModelResponse(parts=[TextPart(json.dumps(reply))])

    def model(self) -> FunctionModel:
        return FunctionModel(self._reply, model_name="scripted")

    def count(self, instructions: str) -> int:
        return sum(1 for i, _ in self.calls if i == instructions)
