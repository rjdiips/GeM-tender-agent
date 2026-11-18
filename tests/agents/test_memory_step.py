from datetime import date

from gem_agent.agents.memory_step import MEMORY_INSTRUCTIONS, MemoryStep, RunSignals
from gem_agent.memory.user_memory import MemoryStore
from tests.agents.helpers import ScriptedLLM

D = date(2026, 11, 18)


def signals(run_id):
    return RunSignals(run_id=run_id, request="AML tenders", understood="anti-money laundering software",
                      kept=["SBI: AML solution"], downloaded=["SBI: AML solution"], restored=[], ignored=["Hospital AML kit"])


async def test_proposals_become_facts_after_two_runs(tmp_path):
    store = MemoryStore(tmp_path)
    add = {"action": "add", "fact": "Interested in bank AML software", "fact_id": None, "evidence": "kept the SBI AML bid"}
    llm = ScriptedLLM({MEMORY_INSTRUCTIONS: [{"changes": [add]}]})
    step = MemoryStep(llm.model(), store)
    await step.after_run("asha", signals("r1"), today=D)
    assert store.prompt_facts("asha") == []
    await step.after_run("asha", signals("r2"), today=D)
    assert [f.fact for f in store.prompt_facts("asha")] == ["Interested in bank AML software"]


async def test_prompt_lists_existing_facts_with_ids_and_the_run(tmp_path):
    store = MemoryStore(tmp_path)
    fact = store.add_stated("asha", "We sell AML software", run_id="r0", today=D)
    llm = ScriptedLLM({MEMORY_INSTRUCTIONS: [{"changes": []}]})
    await MemoryStep(llm.model(), store).after_run("asha", signals("r1"), today=D)
    prompt = llm.calls[0][1]
    assert f"[{fact.id}] We sell AML software (stated" in prompt
    assert "Downloaded: SBI: AML solution" in prompt and "Hospital AML kit" in prompt
