"""Describe what each bid buys from its cached documents, once per document set."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model

from gem_agent.agents.judge import doc_hash
from gem_agent.cache.docs import BidFiles, DocCache
from gem_agent.index.cards import CardOutput, is_generic, rfp_sections
from gem_agent.index.db import BidIndex, Description
from gem_agent.tools.gem.models import Bid

log = logging.getLogger(__name__)

DESCRIBE_PROMPT_VERSION = "1"
DESCRIBE_INSTRUCTIONS = """You say what an Indian government (GeM) tender is buying, from its bid document and RFP.
GeM titles and the "Item Category" are often only a generic category ("Hiring of Agency for IT Projects- Milestone
basis", "Custom Bid for Services"): look past them to the buyer-added terms, the scope of work, the RFP and its file
names.
subject: name what is bought, and for whom if stated. One line, no preamble.
terms: up to 8 specific systems, modules, functions or goods in scope, as the documents name them. Never category or
procurement words such as "IT projects" or "software".
If the text does not say what is being bought, answer with an empty subject and no terms."""


class Describer:
    def __init__(self, model: Model, cache: DocCache, index: BidIndex, *, model_name: str, parallel: int = 3) -> None:
        if parallel < 1:
            raise ValueError("parallel must be at least 1")
        self.cache, self.index, self.model_name = cache, index, model_name
        self._agent = Agent(model, output_type=NativeOutput(CardOutput), instructions=DESCRIBE_INSTRUCTIONS, retries=2)
        self._slots = asyncio.Semaphore(min(parallel, 3))

    async def describe(self, bid: Bid, files: BidFiles) -> Description | None:
        """Store one description; a failed model call or cache read leaves the previous row untouched."""
        try:
            docs = [f for f in files.files if f.role in ("bid_document", "parent_document") and f.status == "ok"]
            bid_text = "\n".join(p.text for d in docs for p in self.cache.load_text(d))[:12000]
            sections, attachments = rfp_sections(SimpleNamespace(cache=self.cache), files, bid_text, 3)
            prompt = (f"Title: {bid.title}\nBuyer: {bid.ministry or ''} / {bid.department or ''}\n\n"
                      f"=== Bid document ===\n{bid_text or '(no readable text)'}" + (f"\n\n{sections}" if sections else ""))
            async with self._slots:
                out = (await self._agent.run(prompt, model_settings={"max_tokens": 400, "temperature": 0.0})).output
            subject = out.subject.strip()
            generic = is_generic(subject, bid.title)
            description = Description(
                b_id=bid.b_id,
                doc_hash=doc_hash(files),
                subject="" if generic else subject,
                terms=[] if generic else out.terms,
                sources=[d.filename for d in docs] + attachments,
                has_scope=bool(sections),
                buyer_org=files.buyer_org,
                model=self.model_name,
                prompt_version=DESCRIBE_PROMPT_VERSION,
                written_at=datetime.now(),
            )
            self.index.put_description(description)
            return description
        except Exception as exc:  # noqa: BLE001 - one failed description never stops the run or refresh
            log.warning("description for %s failed: %s", bid.bid_number, str(exc)[:200])
            return None

    def known(self, b_ids: Iterable[int]) -> dict[int, Description]:
        return self.index.descriptions(b_ids)

    def stale(self, b_id: int, files: BidFiles) -> bool:
        row = self.index.description(b_id)
        return row is not None and row.doc_hash != doc_hash(files)


@dataclass
class RefreshReport:
    described: int = 0
    no_documents: int = 0
    failed: int = 0


async def refresh_descriptions(describer: Describer, index: BidIndex, cache: DocCache, *, limit: int) -> RefreshReport:
    """Refresh stale descriptions from cached documents only; no collector or GeM traffic."""
    report = RefreshReport()

    async def one(bid: Bid) -> None:
        try:
            files = cache.load(bid.b_id)
        except Exception as exc:  # noqa: BLE001 - one unreadable manifest never stops the refresh
            log.warning("description refresh for %s failed: %s", bid.bid_number, str(exc)[:200])
            report.failed += 1
            return
        if files is None:
            report.no_documents += 1
            return
        if await describer.describe(bid, files) is None:
            report.failed += 1
        else:
            report.described += 1

    todo = index.descriptions_to_refresh(describer.model_name, DESCRIBE_PROMPT_VERSION, limit)
    tasks = [asyncio.create_task(one(bid)) for bid in todo]
    try:
        await asyncio.gather(*tasks)
    except (Exception, asyncio.CancelledError):
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return report
