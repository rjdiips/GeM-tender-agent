# GeM Tender Agent — App Flow

How a request moves through the app as built on 17 Nov 2026. The design is in [GEM_TENDER_AGENT.md](GEM_TENDER_AGENT.md); its section numbers are given in brackets.

Solid arrows are the main path; dotted arrows are side effects. The diagram leaves out what is not built yet: the post-run memory step, the harness (7), saved searches and retention (see 14.1 of the spec).

```mermaid
flowchart TD
    subgraph BG["Background (systemd timers)"]
        GEM[(GeM site)] -->|"full load, 15-min sync,<br/>nightly re-fetch"| IDX[(Local bid index<br/>bids.sqlite + title embeddings)]
        IDX -.->|"vague titles"| CARDS["Bid cards<br/>(model reads docs)"] -.-> IDX
    end

    U([User in web chat]) -->|"POST /api/ask"| RM["RunManager.ask"]

    subgraph UND["Understand (before any run)"]
        C1["Chat call 1: intent, 1-3 meanings,<br/>date window, buyer hints, stated facts"]
        PR["Probe each meaning<br/>against the local index"]
        C2{"2+ meanings left?"}
        C3["Chat call 2: keep 1 or 2"]
        C1 --> PR --> C2
        C2 -->|yes| C3
    end
    RM --> C1
    C1 -.->|"stated facts"| MEM[(User memory)]
    MEM -.->|"prompt context"| C1

    C2 -->|no| INT
    C3 --> INT{"Intent?"}
    INT -->|"show_rejected / memory / other"| ANS["Canned reply or switch tab<br/>(no run)"]
    INT -->|"search / refine"| RUN["Create run record<br/>start background task"]

    subgraph GRAPH["Run graph (langgraph, SQLite checkpoint)"]
        PL["plan<br/>Planner: categories, ministries,<br/>extra terms (enum-checked)"]
        SE["search<br/>word + meaning match in index,<br/>model screens titles,<br/>closing soonest first"]
        AS{"Empty or<br/>over cap?<br/>(max 2 retries)"}
        RT["retry: widen / narrow"]
        subgraph PROC["process (a few bids in parallel)"]
            CO1["Collector: bid PDF only"]
            TR{"Judge triage<br/>(pages 1-2)"}
            CO2["Collector: attachments,<br/>corrigendum, OCR"]
            JU["Judge: passages to verdict<br/>with 1-3 checked quotes"]
            CO1 --> TR
            TR -->|"clear no"| OUT
            TR -->|"maybe / yes"| CO2 --> JU --> OUT["Save outcome"]
        end
        REP["report: 'Done: N relevant,<br/>M quarantined'"]
        PL --> SE --> AS
        AS -->|yes| RT --> PL
        AS -->|no| CO1
        OUT --> REP
    end
    RUN --> PL
    IDX --> SE
    IDX --> PR
    GEM --> CO1
    GEM --> CO2
    CACHE[(Doc + verdict cache)] <--> CO2
    CACHE <--> JU

    OUT -->|"result events"| SSE["Event store + bus<br/>SSE /api/runs/id/events"]
    REP --> SSE
    SSE --> UI["Web UI: results panel,<br/>quarantine (restore), downloads / zip"]
    UI -.->|"restore, refine"| RM
    UI -.->|"view / forget facts"| MEM
```

## Steps

1. **Local index, in the background** (6.2): `index/sync.py` does a full load of GeM's open bids once, then syncs every 15 minutes and re-fetches everything nightly. New titles get embeddings. For vague titles, `index/cards.py` writes a bid card from the bid's documents. Searches read this index, not the live site.
2. **Understand** (5.3): `agents/chat.py` turns the message into an intent and 1–3 candidate meanings. It checks each meaning against the index, and a second call chooses between them only if two or more are left. Stated facts go straight into user memory.
3. **Run or reply** (`graph/manager.py`): a search or refinement creates a run that executes as a background task. Anything else (show rejected, memory questions) gets a reply on the current stream and starts no run.
4. **Run graph** (`graph/build.py`, 5.1): plan → search → (widen or narrow, at most 2 retries) → process → report. Every step is checkpointed, so a run interrupted by a crash or restart resumes when the app starts.
5. **Process each bid** (5.4): the Collector fetches the bid PDF and triage reads its first pages. Only bids that pass have their attachments fetched and a full verdict written, with quotes checked against the cited pages. Documents and verdicts are cached across runs and users.
6. **Stream** (4.1): each outcome is streamed to the web UI over SSE as soon as it is saved. `/api/runs/{id}/events` replays a run and then follows it live, so the page can be rejoined.
7. **Borderline bids** (4.1, 5.5): a bid the Judge cannot call without the user comes back as borderline with a question. The user rules "counts" or "doesn't count" in the web UI; the ruling becomes a per-topic memory fact, settles the run's other bids that raise the same question, and decides similar bids in later runs. Undo removes a fact the click created and un-settles what it settled. The run never waits.
8. **After the run**: the user restores quarantined bids, downloads files or a zip, refines the request (a follow-on run that reuses the cache), and views or forgets memory facts.
