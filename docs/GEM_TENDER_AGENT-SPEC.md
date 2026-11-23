# GeM Tender Agent — Spec

Status: draft, updated 17 Nov 2026 to match the app; 18 Nov: live search decided (6.2); built 19 Nov 2026. Every site fact in section 3 was checked against the live GeM site on 13 or 16 Nov.

**Built so far (17 Nov):** sections 3–6 (HTTP clients, live GeM search with a bid cache (6.2, built 19 Nov; it replaced the local index with its sync and nightly re-fetch), Collector, Judge, Chat, Planner, Searcher, user memory, langgraph graph), the web chat (4.1), quarantine with restore, and the zip download.
**Not built yet** (the modules exist as one-line stubs): the harness of section 7 (Monty host modules, discovery, skills, checks; so R8 is not met), the browser fallback (6.1), the retention clean-up (11.1), saved and scheduled searches (10, A.2 L1), and the nightly pre-fetch for saved topics (6.3). Sections that describe these say so.
**Described as working but not wired up** (found 17 Nov): the post-run memory step, the daily request budget, the run log, the Reporter's summary, corrections mid-run, the daily closing-date re-check, and deleting by hand. See 14.1.
**Waiting on review:** the labelled set's flagged labels and two disagreements (section 15).

**What we're building is a harness.** The harness is the agent loop around the model and holds:
- the tools the model can call;
- a library of **skills**: code the model wrote that worked and is reused;
- the Monty sandbox, where the model writes new skills;
- the checks that decide whether a skill really worked;
- the memory.

GeM tenders are the first job the harness does. The tender agents (section 5) are users of the harness, not the harness itself.

## 1. Goal

A chatbot where a user describes the tenders they want, in plain language, for example:

> "Download tender documents relating to anti money laundering published within the last 2 weeks."

The system then:

1. Works out what the user means (no questions asked), and turns it into searches.
2. Finds every matching open bid on GeM.
3. Downloads each candidate bid's PDF and all its attachments (RFP, scope of work, corrigenda).
4. Reads the documents and decides whether each bid really matches the request. Bids that don't match are moved to a quarantine folder, with the reason recorded.
5. Replies with a summary of the matching bids, with each one's documents available to download.

Because the GeM site may change, the agents must be able to work out a new way to do any step. They do this by writing their own code in a sandbox and keeping what works as skills (section 7).

### Out of scope (for now)

- CPPP / GePNIC tenders (and the captcha solver in `CPPP_ACCESS_AND_CAPTCHA.md`), and banks' own tender pages. These come later as extra sources.
- Closed or awarded bids. GeM's public search only covers open bids.
- Running new third-party libraries. The Docker sandbox for that is deferred until it's needed (7.5).
- Bidding, or anything that needs a GeM login.

## 2. Requirements

| ID | Requirement |
|---|---|
| R1 | The user asks in plain language; dates can be relative. "Within N weeks" means **published** within the last N weeks unless the wording says otherwise ("closing within…"). |
| R2 | Site filters use real site values: categories and ministries from the site's own lists. The system never invents a value. |
| R3 | The search covers every matching open bid, not just the first page. |
| R4 | For every candidate bid, the bid PDF plus every attachment linked from it is downloaded. |
| R5 | Relevance is decided by reading the documents, not the title alone. Each decision is stored with the evidence (quoted passages, file and page). |
| R6 | Bids that aren't relevant go to a quarantine folder, kept 30 days and then deleted. The decision record is kept permanently. |
| R7 | The reply lists each relevant bid with its number, buyer, published and closing dates, value details, a one-line reason, and download links. A zip of everything is also offered. |
| R8 | If a built-in tool fails, the agent can write its own code in the Monty sandbox. Code that works is saved as a skill and tried first next time. (Not met yet: section 7 is not built.) |
| R9 | Polite traffic: one client per GeM host, ≥1 s between requests to the same host (verified, 3.2), slowing to 4 s on any real error. A 404 with "No data found" means an empty result, not an error (3.2). |
| R10 | Every run is logged: what the system understood, searches run, files fetched, decisions made, and which tool or skill did each step. |
| R11 | **The system works out the user's meaning itself and never asks the user to confirm it.** It states the meaning it used in its first progress line. A document matches on meaning, not on a string (5.3). |
| R12 | First results reach the user within a few minutes, even for a large request (section 8). |
| R13 | The model is used as little as possible: cheap local steps filter first, and the model reads only what is left (section 9). |

## 3. What we verified on the live site

### 3.1 Two ways to list bids

**A. Advanced search** (`https://bidplus.gem.gov.in/advance-search`). No captcha.

```
POST https://bidplus.gem.gov.in/search-bids
  payload        = JSON (below)
  csrf_bd_gem_nk = token embedded in the page's JavaScript
```

| Tab | `searchType` | Payload fields |
|---|---|---|
| Search by Bid / RA Details | `bidNumber` | `bidNumber`, `category`, `bidEndFrom`, `bidEndTo` |
| Search by Ministry / Organization | `ministry-search` | `ministry`, `buyerState`, `organization`, `department`, `bidEndFromMin`, `bidEndToMin` |
| Search by Consignee Location | `con` | `state_name_con`, `city_name_con`, `bidEndFromCon`, `bidEndToCon` |
| Search by BOQ Title | `boq` | `boqtitle_con`, `bidEndFromBoq`, `bidEndToBoq` |

All four also take `page` (1-based). Dates use the format `YYYY-MM-DD` (`DD-MM-YYYY` gives the same result); only closing dates can be filtered. The BOQ tab also sends `bidvalue` (empty).

**B. All bids list** (`https://bidplus.gem.gov.in/all-bids`), via `POST /all-bids-data`. Its payload is `{page, param: {searchBid, searchType: fullText|exact}, filter: {bidStatusType: "ongoing_bids", byType, highBidValue, byEndDate: {from, to}, sort}}`.
- `sort` can be `Bid-Start-Date-Latest`, `Bid-Start-Date-Oldest`, `Bid-End-Date-Latest` or `Bid-End-Date-Oldest`. **Newest-first sorting works.**

**Common to both:**
- **Page size is fixed at 10.** `rows` and `limit` are ignored.
- **Responses are fast:** 0.1–0.35 s. The gap between requests is our own choice; see 3.2 for what GeM tolerates.

Lookup lists:

| Endpoint | Gives |
|---|---|
| `ministry-list-adv` | 77 ministries, with duplicate spellings. `FINANCE` returns **no bids**; `Ministry of Finance` returns 901. |
| `org-list-adv` (`ministry=` or `buyer_state=`) | Organisations under a ministry or state |
| `dept-list-adv` | Departments |
| `state-list-adv`, `city-list-adv` | Consignee locations |
| `boq-title-adv` | BOQ titles |
| `<select id="categorybid">` in the advance-search HTML | 14,286 categories, as value/label pairs (e.g. `services_home_itse_hi12433188` = "Hiring Of Agency For It Projects- Milestone Basis") |

All lookups are POSTs carrying `csrf_bd_gem_nk`. `ministry-list-adv` returns both `MinistryList` and `BuyerStateList`; `org-list-adv` and `dept-list-adv` include `N/A` / `null` entries, which the site hides and so do we.

Volumes (16 Nov):

| Measure | Value |
|---|---|
| Open bids in total | 44,237 |
| New bids per day | about 2,600. Page 800 of the newest-first list reached 12 Nov 15:52, page 1600 reached 9 Nov 13:59. |
| Published in the last 2 weeks | Most open bids (≈36k), since bids usually stay open 2–4 weeks |
| Category "Hiring of Agency for IT Projects- Milestone basis" | 164–167 bids |
| Ministry of Finance | 901–943 bids; 798 of them closing 13–27 Nov |

Each result includes:
- `b_id`, `b_bid_number`, `b_category_name` (the bid's title / items);
- `ba_official_details_minName`, `ba_official_details_deptName`;
- `final_start_date_sort` (published), `final_end_date_sort` (closing; it already includes corrigendum extensions);
- `b_bid_type` (1 = bid, 2 = reverse auction, 5 = direct RA), `b_id_parent` / `b_bid_number_parent` (for an RA, the original bid);
- `b_eval_type`, `b_total_quantity`, `is_high_value`, `b_status`.

Every field comes as a one-item list, and some are missing (`ba_official_details_minName` often is). Timestamps end in `Z` but are **IST clock time**: the site's own page renders them with `timeZone: 'UTC'` (e.g. `2026-11-23T16:00:00Z` is shown as 23-11-2026 4:00 PM, matching the corrigendum's "2026-11-23 16:00:00"). Reverse auctions in the newest-first list can have a published (start) date in the future: on 16 Nov the list opened with RAs starting 17 Nov 13:00.

### 3.2 Behaviour that matters

- **An empty result comes back as HTTP 404**, with the body `{"status":0,"code":404,"message":"No data found"}`. It must not be treated as a rate limit or a block. (An earlier conclusion that "plain scripts are blocked" came from exactly this: a search on the empty `FINANCE` entry.)
- **Plain HTTP works.** No browser is needed: a Python `urllib` client with a cookie jar opened the page, read the token, searched (200, 164 and 901 results) and downloaded a bid PDF (104 KB in 1 s).
- **GeM's keyword search is useless for meaning.** "anti money laundering" returns 232 bids that each contain one of the words (anti-smog guns, anti-seize paste). "loan origination" returns 2 unrelated bids. It searches title words only.
  - **Its counts are erratic (measured 18 Nov, open bids, the 8 labelled topics' terms; `eval/live_search/`):**

    | Search | Bids |
    |---|---|
    | "KYC" | 0 |
    | "Know Your Customer" | 710 |
    | "Anti-Money Laundering" | 0 |
    | "AML" | 91 |
    | "AI Platform", "AI Software", "AI Infrastructure" | 2,880–2,885, identical results |
    | "Department of Revenue" and other department names | about 33,000, nearly the whole list |

  - **`exact` mode returns 0 for every title term.** It only matches ministry and department names: "Ministry of Finance" returns 932, "Department of Financial Services" 473.
  - **No match-count threshold separates useful searches from noisy ones.** A 300-bid cap per search would have lost 6 of 12 relevant AI bids, which came only from the 2,880-bid "AI …" search, and 1 of 5 Ministry of Finance bids.
  - **Keyword terms find no vague-title bid.** The relevant AML, fraud, KYC and LOS bids are all filed as "Hiring of Agency for IT Projects", and their keyword terms found 0 of them.
  - **A multi-word query appears to match on its first word only** (18 Nov, strong evidence, not proven):

    | Query | Bids | Single first word | Bids |
    |---|---|---|---|
    | "Corporate Internet Banking", "Corporate Banking Portal", "corporate restructuring" | 19 each, the same bids | "corporate" | 20 |
    | "payment gateway", "payment switch", "payment hub" | 6 each, the same bids | "payment" | 7 |
    | "Core Banking Solution" | 497 | "core" | 518 |
    | "digital payment" | 803 | "digital" | 806 |

    The small differences are new bids published between the two runs. Word order changes the result: "banking corporate" returns 57.

    Two results do not fit:
    - "AI" on its own matches 22,562 bids, against 2,880 for "AI Platform", so very short tokens behave differently.
    - "Treasury Management System" returned 0, and "treasury" on its own was not checked.

    So the Planner's phrase terms mostly act as searches for their first word, and two phrases that start with the same word give the same search.
- **Category and buyer searches (measured 18 Nov):**
  - **Category "Hiring of Agency for IT Projects- Milestone Basis"** (`services_home_itse_hi12433188`): 171 bids, 18 pages, 18 s. 162 are vague-title bids. It reached every relevant vague-title bid that was still open (9 of 10; the tenth had closed that day) and 243 of the index's 247 open vague-title bids.
  - **Category "Custom Bid For Services"** (`services_home_cust`): 2,497 bids, 250 pages, only 86 of them vague, none relevant.
  - **Keyword search on either category name** reaches the same vague bids at about 10 times the pages.
  - **`exact` department searches** found all 5 relevant Ministry of Finance bids: Department of Revenue 243 bids, Department of Financial Services 473.
  - **Some bank bids are filed under a separate department, "BANKING SECTOR"** (Ministry of Finance, 54 bids on 18 Nov). This is not the Department of Financial Services: PNB's CBS upgrade and BOBCARD's card platform were listed there. `exact` is case-sensitive, so "Banking Sector" returns 0.
  - **"Hiring of Consultants – Milestone/Deliverable Based – …" is not one category.** Each such bid has its own category value, as with "Custom Bid for Services". A `fullText` search for "consultants" returns them: 200 bids on 18 Nov. Mazagon Dock's transaction cum legal advisory, an M&A-type bid, is one of them, and "transaction advisory" returns it too (2 bids).
- **Listing speed (18 Nov):** 1,638 pages at the 1 s floor had a median of 1.0 s per page and a 90th percentile of 1.26 s.
- **Tabs don't combine.** One request carries one `searchType`. The results carry ministry, department and category, so the system filters those itself.
- **Transient 500s happen.** On 16 Nov one ministry search returned HTTP 500 with an empty body; the identical request a minute later returned 906 bids. Retrying real errors (6.1) is needed, not optional.
- **GeM tolerates a 1 s gap (verified 16 Nov, plain HTTP, one client).**

  | Test | Result |
  |---|---|
  | 20 listing requests each at 2 s, 1 s and 0.5 s gaps | all 200; median 0.26–0.30 s |
  | 10 bid PDF downloads at 1 s | all valid PDFs (100–140 KB); median 0.82 s |
  | 150 listing requests in a row at 1 s (197 s) | all 200; median 0.32 / 0.28 / 0.26 s by thirds (no slowdown); none over 1.5 s |

  No throttling, blocking or slowdown appeared. 0.5 s also worked, but 1 s is the floor we use: it's fast enough (section 8) and leaves margin. The limiter is adaptive. Any real error (5xx, 429, 403, or a 404 without "No data found") or a latency spike (median over 2 s) drops that host to 4 s. After 10 minutes clean, it steps back down. Old fetch scripts saw "404s when too fast"; those were most likely "No data found" responses.
- **Publish date isn't a site filter.** The newest-first sort on `all-bids-data` gives it: page until the published date is past the window.

### 3.3 Documents

| Bid type | Document URL |
|---|---|
| Bid (`b_bid_type` 1) | `/showbidDocument/<b_id>` |
| Reverse auction (2) | `/showradocumentPdf/<b_id>`, or `/list-ra-schedules/<b_id>` when `b_eval_type` > 0. The original bid is `/showbidDocument/<b_id_parent>`. |
| Direct RA (5) | `/showdirectradocumentPdf/<b_id>` |

Verified: an RA's `showbidDocument` returns an empty PDF (0 bytes), while `showradocumentPdf` returns the real one (129 KB).

- **Attachments** are links inside the bid PDF, read from its link annotations with pypdf. Seen so far (checked 16 Nov by fetching one of each):
  - `bidplus.gem.gov.in/resources/upload_nas/.../biddoc/bid-<b_id>/<n>.pdf` (buyer's scope, RFP): PDF
  - `bidplus.gem.gov.in/bidding/bid/downloadBuyerDoc/<b_id>/<n>.pdf` and `.../downloadBuyerTechDoc/...`: PDF
  - `fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=...` (SLA / RFP): PDF
  - `admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=YYYYMMDD` (GeM general terms: the same for every bid on a date, so download once and cache): PDF
  - `bidplus.gem.gov.in/bidding/bid/bidsla/<n>` (service-level agreement text) and `.../bidding/bid/showCatalogue/<token>` (item specification): **HTML pages that are real documents**, not error pages
  - `bidplus.gem.gov.in/resources/upload_nas/.../bidding/excel/bid-<b_id>/<n>.xlsx`: bills of quantities / commercial annexures (xlsx); RFPs also come as docx. Office files are zip files inside, recognised by their OOXML manifest, so they aren't mistaken for archives.
  - `bidplus.gem.gov.in/bidding/downloadOmppdfile/` with no id: 0 bytes
  - `mkp.gem.gov.in/catalog_data/...` buyer documents (not yet fetched)
  - An RA's PDF links to its original bid (`/showbidDocument/<b_id_parent>`).
  - Off-site links (e.g. `tcpdf.org`, `bifma.org`) are dropped by the host allowlist.
  - File types are told apart by content, not by the server's content type, which says PDF or HTML even for 0-byte bodies.
- **Placeholder attachments are common.** Of 199 files surveyed, 21 are one-line placeholders ("As per Buyer ATC", "Please refer the RFP uploaded in…"). They are recognised as placeholders by their content and skipped by the Judge. Rule (from that survey): one page with under 200 characters of text. Every placeholder had at most 70 characters; the smallest real document had 732. A file with no text at all is not a placeholder (it may be an unreadable scan). The same placeholder is often uploaded under several links of one bid (GEM/2026/B/8162911 has three).
- **Corrigenda:**
  - `POST /public-bid-other-details/<b_id>` returns `{"corrigendum": true|false}`. It's a cheap check.
  - `POST /bidding/bid/viewCorrigendum/<b_id>` returns HTML with the changes, e.g. "Modified On: 2026-11-09 … Bid extended to 2026-11-23 16:00:00", or the body `0` when there is none. Verified on GEM/2026/B/8162911.

### 3.4 Document text

Survey of the 199 files (10,541 pages) downloaded earlier:

- **Scanned files:** 5 files (38 pages), all from one bid (AIC's cloud-hosting RFP), have no text layer. Tesseract (English) OCR at 200 dpi read the first page cleanly. Only pages with almost no text get OCR.
- **Hindi:** 63 files contain Devanagari. pdftotext and PyMuPDF both garble it, because of legacy font encodings (e.g. "कायाालय", "यूवियि"). The documents are bilingual, though, so the English text carries the content. Tesseract has no Hindi language pack installed (`eng`, `msa` only), so Hindi OCR is out of scope for now.
- **Multilingual embeddings are available locally:** `BAAI/bge-m3` and the reranker `bge-reranker-v2-m3` are already in the Hugging Face cache, and Hugging Face is reachable.

### 3.5 Models and compute

| Item | Value |
|---|---|
| LLM | `qwen/qwen3.6-35b-a3b` at `http://10.0.10.11:1234/v1`. It is already loaded, and we use only this model. |
| Server | vLLM, running on this same machine (10.0.10.11 is this host; checked 16 Nov: `vllm serve … --max-model-len 32768 --max-num-seqs 6 --gpu-memory-utilization 0.95`). Only the served model name is ever requested. |
| Prompt limit | About 32k tokens |
| Speed | 1–8 s per agent turn in the Monty tests below |
| Local machine | One RTX 4090 (24 GB), driver 595.99, CUDA 13.2; the project installs the CUDA build of torch (2.15.0+cu132, GPU visible, checked 16 Nov). vLLM reserves 95% of GPU memory, leaving ≈0.8 GB, which is less than bge-m3 (≈1.1 GB fp16) needs, and at 95% even a CUDA context cannot be created (out of memory, checked 16 Nov). **Ruled out by the user on 17 Nov: the model on the GPU and vLLM's settings are never touched.** The Judge therefore ranks pages with **all-MiniLM-L6-v2 on the CPU** (`judge_cpu_embed_model`), or a remote embedder when `EMBED_BASE_URL` is set; the 20 Nov A/B that settled this is in 3.5.1. Running embeddings on the GPU would mean lowering vLLM's `--gpu-memory-utilization` (e.g. to 0.88, freeing ≈1.7 GB) and shrinking its KV cache. |
| Chat template | Qwen's template accepts one system message, and only as the first message. pydantic-ai's retries used to add a second system message mid-conversation, and the server rejected every retry (HTTP 400). The model profile sets `supports_inline_system_prompts=False` and `default_framing_announcement="never"` (`agents/models.py`). |

Tool calling works. On 13 Nov, "AML software for banks, ending within 2 weeks" gave the right dates but an invented category, so the Planner may only use categories returned by `find_categories`.

#### 3.5.1 Embedding and reranker A/B (20 Nov)

**Question.** Which page-ranker configuration puts the page a verdict quotes into the Judge's top 8, per second of CPU?

**Harness.** `eval/retrieval_ab.py` calls the real `find_passages` (BM25 + dense + RRF, optional rerank) over chunks already in `data/cache`: no crawling, no LLM calls, no verdict cache. Cases are the labelled set's quoted pages — 247 usable `(file, page)` pairs over 156 bids (38 labelled relevant, 29 unsure, 28 traps, 137 live-only negatives). Target is the quoted page; metrics are hit@8 (the 5.4 metric), hit@24 and MRR, with a 95% CI from a bootstrap clustered by bid. `eval/retrieval_ab/README.md` documents the protocol and the pitfalls (the verdict cache does not include the model; the chat model has no sampling settings).

**Runs** (model host, 20 Nov; TEI `cpu-1.9`, 6 CPUs per container):

| Configuration | relevant hit@8 | relevant hit@24 | MRR@24 | full hit@8 | full hit@24 | p50 / bid |
|---|---|---|---|---|---|---|
| words only | 36.8% | 42.1% | 0.204 | 9.3% (23/247) | 10.9% | 0.1 s |
| **MiniLM, in-process** | **47.4%** | **65.8%** | **0.297** | — | — | **1.9 s** |
| bge-small over TEI | 47.4% | 60.5% | 0.218 | 19.0% (47/247) | 38.5% | 4.3 s |
| bge-small + bge-reranker-base | 39.5% | 60.5% | 0.261 | 17.0% (42/247) | 38.5% | +4.0 s |
| bge-small + bge-reranker-v2-m3 | 39.5% | 60.5% | 0.261 | — | — | +16 s |

**Paired tests** (same cases, bid-clustered bootstrap): MiniLM − words = +10.5 pp [−2.9, +24.4]; bge-small+reranker − words = +10.5 pp [+2.4, +21.6]; MiniLM − bge-small ≈ 0.0 pp (2 cases each way). Reranker against fused order, full set = −2.0 pp [−6.5, +2.2] — 13 targets promoted into the top 8, **18 demoted**, including pages fused had at rank 1, 3, 4, 5 and 7 pushed to 9–20; on the relevant cases, −7.9 pp [−21.6, +5.6].

**Decisions.**

- The Judge's default page ranker is **all-MiniLM-L6-v2, in-process**: the same hit@8 as bge-small and bge-m3, a better hit@24 and MRR, and about twice the speed. The app no longer loads bge-m3; the GPU stays with vLLM.
- **The reranker stays off.** It cannot add candidates (it only reorders the fused 24), loses top-8 recall on both slices, and costs 4 s (base) to 16 s (v2-m3) per bid on top of the embedding step.
- Latency, not quality, is the open issue: p95 is 13.5 s (MiniLM) and 27.7 s (bge-small) on bids with 1 300+ chunks. The next thing to try is a BM25 prefilter — embed only the top ~200 chunks — which keeps hit@8 and bounds the dense cost.
- The CPU TEI pair in `deploy/tei/` remains available for moving embedding work off the app host; it does not improve hit@8 over local MiniLM, so it is an operational choice, not a quality one.

**Deployment.** `deploy/tei/` runs the embedder in TEI. `docker compose up -d` starts it on
the CPU; the GPU variant is behind the `gpu` profile and needs `docker compose stop embed &&
docker compose --profile gpu up -d embed-gpu`, because TEI builds separate CPU and CUDA images
(no `--device` flag) and Compose cannot choose from live VRAM — there is no auto mode. With vLLM
at 0.95 the card has had 0.25–0.9 GB free, so the CPU path is the only one usable in practice.
TEI serves the same all-MiniLM-L6-v2 with the same 256-token window as the in-process path
(the reranker container is behind the `ab` profile and not started by `docker compose up -d`),
so the quality above applies unchanged; it measured 103 chunks/s on 6 CPUs (≈1.6 s for a p50 bid),
against ~21 chunks/s for the in-process path during the A/B. The choice between local and
remote is therefore operational (where the CPU is spent), not a quality one.

**Caveats.** 38 relevant cases; the metric is a page hit, not a verdict; an end-to-end verdict A/B would need repeated runs. Rerun with `bash eval/retrieval_ab/run_ab.sh` (finished configs are skipped and the embedding vectors are cached).

### 3.6 Can Qwen write skills in Monty? (16 Nov experiment)

**Set-up:**
- Goal given to the model: "find open bids in category *Hiring Of Agency For It Projects- Milestone Basis*, first 2 pages, return bid number, `b_id` and closing date".
- The model was not told any endpoint. Its only access was host functions in Monty.
- Our check: ≥10 rows, valid bid numbers, numeric `b_id`, no duplicates.

| Run | Tools given | Turns allowed | Result |
|---|---|---|---|
| 1 | raw HTML + regex search only | 8 | Failed: grepped a 1.5 MB page blindly |
| 2 | + `forms`, `options`, `set_value`, `click`, `network` | 15 | Failed: it saw the Category dropdown on turn 1 but kept grepping HTML |
| 3 | user-like tools only (raw HTML withheld) | 15 | Failed, but close: found the category value on turn 3, then pressed "View All" instead of "Search" |
| 4a | same | 25 | **Passed on turn 12** (44 s, 30k prompt tokens): read the page's own request from `network()` and repeated it for page 2 |
| 4b | same | 25 | Failed: clicked Search repeatedly without reading `network()` |

**The passing code, turned into a skill:**
- The code worked, but it **hard-coded the session's CSRF token**, so it would break in any new session.
- The model was asked to turn it into a reusable function `run(category, pages)` that reads the token at runtime. On the first replay in a fresh session the function failed: it had guessed where the token sits.
- With a **repair loop** (the error goes back to the model, which can probe the page and redefine `run`), it found the token on its third attempt.
- The function then returned 0 rows for its test category, Cyber Security Software. That was correct: that category has no open bids (GeM said "No data found"). Our check, which demanded ≥10 rows, was what was wrong.

**Lessons, all built into section 7:**
1. **Tools must be what a person sees:** forms, dropdown options, click, and the page's network calls. Withholding raw HTML by default made the model far more focused.
2. **Discovery succeeds about half the time within 25 turns.** So the harness retries discovery (3 attempts) and detects when the model is stuck repeating itself.
3. **A pass isn't a skill yet.** The code must be turned into a function, scanned for values tied to one session (tokens, cookies), and **replayed in a fresh session on different inputs** before it's saved.
4. **Checks must allow legitimate empty results.** An empty result is confirmed by GeM's own "No data found", not rejected.
5. **Discovery is cheap enough as a fallback** (30–100k prompt tokens, under a minute per attempt). A saved skill replays with **no model calls at all**.

## 4. User flow

1. **The user asks.** Example: "LOS tenders published in the last 2 weeks."
2. **The system starts at once.** It doesn't ask the user to confirm the meaning or approve a plan. It works out the meaning (5.3) and the dates.
3. **Its first progress line says what it understood.** Example: "Looking for *Loan Origination System* tenders published 2–16 Nov (36,1xx open bids in that window; 48 look relevant from their titles)." This is information, not a question. If the user corrects it mid-run ("no, I meant …"), the run is re-planned and keeps any downloads that still apply. (Today a correction starts a follow-on run instead; see 14.1.)
4. **Results stream in.** Each relevant bid appears as soon as it's judged, with bids closing soonest first.
5. **The final reply** is a summary table, download links (per bid and a zip), and a short note on bids that were quarantined and why. (Today it is one count line; the table and links are in the results panel. See 14.1.)

Follow-ups reuse the run ("show me the rejected ones", "only central banks"). Documents and verdicts are cached across runs and users (section 6.3).

### 4.1 Web chat (decided 16 Nov)

The UI is a web chat. A FastAPI backend serves a React front end (the same pattern as Automation Studio).

**Design (chosen 16 Nov from prototypes, `docs/prototypes/chat-ui/`):** "Midnight pro" in dark and light modes. Three columns: history sidebar, chat thread, results panel. Monospace bid numbers, closing times and a timestamped progress log; glass cards; an emerald accent; urgency tags ("2d"); "2 quotes · from documents" per result with evidence expanding inline. The proof label (changed 17 Nov from a "% sure" figure, which was the model's own number and almost always ≥85%) says where a relevant verdict's verified quotes come from: **documents**, **title only**, or **none found**; the last two are shown in the warning colour. Each quote that fails its check is labelled "too short to count" or "not found in documents". While a new request is being understood, the thread shows "Reading your request…" and the results panel shows placeholder cards. The mode follows the operating system until the user picks one in the header.

- **Chat panel:** the user types requests and follow-ups. Progress lines and results stream in over server-sent events while the run works: what was understood, shortlist size, each bid as it's judged.
- **Results table:** bid number (linked to GeM), buyer, published and closing dates, EMD and turnover, a one-line reason, and an evidence popover with the quoted passages and their pages. It also has download buttons per file, per bid, and a zip of all.
- **Borderline section (18 Nov):** at the top of the Relevant tab, and only when it has bids. Each borderline bid shows the one question the Judge could not answer for the user (for example, "AI is one part of a digital twin platform. Does that count as an AI platform for you?") with two buttons, **Counts for me** and **Doesn't count**. The buttons work while the run is still going. A bid settled by an earlier ruling shows that ruling's text and an undo link. The Relevant tab shows its count and, while bids wait for a ruling, how many are left (Relevant 12 · 3 to decide), and after a run with unruled bids the thread gets one line ("3 bids need your call: see Borderline."). A run never waits for a ruling and the chat never asks. Borderline bids stay out of the zip, the relevant count and the run list's `found` until the user rules "counts"; a "doesn't count" ruling quarantines the bid with the reason "Your ruling: …".
- **Rejected tab:** quarantined bids with the reason; one click restores a bid. A restore is also a memory signal (5.5). Restoring a bid that was ruled "doesn't count" changes the ruling to "counts".
- **History sidebar:** past runs and saved daily searches, with "new since last run" counts.
- **Memory panel:** "What I remember about you". The facts with their evidence, each with edit and forget buttons.
- **Sign-in:** each user sees only their own runs, downloads and memory. Login uses the company's existing auth if one is available; otherwise a simple username login on the LAN to start.

## 5. Architecture

### 5.1 Orchestration (langgraph)

```
 user ─► Chat ─► Planner ─► Searcher ─► Collector ─► Judge ─► Reporter ─► user
                    ▲          │            │           │
                    └─ widen/narrow ────────┴───────────┘
```

- **One langgraph graph per run.** State is a Pydantic `RunState`, saved with the SQLite checkpointer so a run resumes after a crash.
- **The graph never pauses to ask.** A user message during a run is fed into the running graph, and the Planner re-plans from it.
- **Parallel work:** Collector and Judge fan out per bid. Site traffic is limited per host (R9); judging overlaps downloads.
- **Going back to the planner:** if the shortlist is empty or larger than a cap (default 150), the graph returns to the Planner to widen or narrow. At most 2 retries.

### 5.2 Agents (pydantic-ai)

| Agent | Input | Output | Tools |
|---|---|---|---|
| **Chat** | user message, history | `TenderRequest`: `TopicDefinition` (5.3), buyer hints, date window (published or closing), limits | `expand_topic`, `probe_live(meaning)`: page 1 of live searches (6.2; was `probe_site` on the index) |
| **Planner** | `TenderRequest` | `SearchPlan`: index query (date window, ministries, departments, bid types) + semantic query + optional site searches (category values) + reasons | `find_categories(query)`, `list_ministries()`, `list_orgs(ministry)`, `list_departments(...)` |
| **Searcher** | `SearchPlan` | ranked shortlist of `Candidate`s, in two stages (6.2) | live GeM searches: base searches, then the plan's categories, buyers and terms; title screen; bid cache |
| **Collector** | `Candidate` | `BidFiles`: documents, corrigendum, text (OCR where needed), links | `gem.bid_document`, `gem.attachment_links`, `gem.download`, `gem.corrigendum`, `pdf.text` |
| **Judge** | `TopicDefinition` + `BidFiles` | `Verdict`: relevant yes/no, confidence, reason, evidence quotes (file + page), key facts (EMD, turnover, period, scope summary) | `find_passages(bid, query)`, `read_pages(file, pages)` |
| **Reporter** | verdicts | summary + zip | `make_zip(run)` |

Planner notes:
- **18 Nov:** for 4 of 5 banking classes the Planner chose no category and no buyer, so it never searched "Hiring of Agency for IT Projects" or the bank departments. Those hold most relevant bank tenders (3.2, 6.2).
- `find_categories` searches the 14,285 categories by meaning and keyword and returns exact site values. Categories are optional extra searches, not the main filter. With live search (6.2), bids under vague categories are reached by the base searches; the Planner's terms run as live title searches, so a term's first word should be specific.
- **Ministry spellings:** all spellings of a ministry are included; empty ones cost one request each and return "No data found".

### 5.3 Working out what the user means

Example: "LOS tenders" must mean Loan Origination System. It must not match "LOST", "loss", "Line of Sight" or "Level of Service".

1. **The topic is expanded before any search.** The Chat agent writes a `TopicDefinition` from its own knowledge plus context:

   ```json
   {
     "user_text": "LOS tenders",
     "meaning": "Loan Origination System: software banks/NBFCs use to capture, assess, approve and disburse loan applications",
     "domain": "banking / lending IT",
     "match_terms": ["Loan Origination System", "LOS", "loan origination", "digital lending platform",
                     "loan application processing", "credit underwriting workflow"],
     "acronyms": {"LOS": "Loan Origination System"},
     "related_but_not_enough": ["Loan Management System (LMS)", "core banking", "credit bureau integration"],
     "not_this": ["LOST (lost/found)", "loss", "Line of Sight", "Level of Service", "Length of Stay"],
     "chosen_because": "user memory: past downloads are all bank/NBFC IT tenders; current GeM bid titles match this meaning",
     "other_meanings_searched": []
   }
   ```

2. **The system picks the meaning itself, without asking.** It weighs:
   - **What the harness has learned about the user** (user memory, 5.5). For example, "every tender this user kept was bank or NBFC IT" makes AML mean anti-money laundering, not acute myeloid leukaemia. This memory starts empty and is built from use.
   - **The rest of the request:** "LOS for cooperative banks" settles it; "LOS survey equipment" points the other way.
   - **Earlier requests** in the chat, and saved meanings (below).
   - **What the site has:** each plausible meaning is checked with page 1 of up to 3 of its terms, searched live (6.2; until 18 Nov this was `probe_site` on the local index). A meaning with real matching tenders gains weight. This costs 1–3 requests per meaning.

   **When two meanings stay plausible,** the system searches both and groups the results, likeliest first: "LOS as Loan Origination System: 4 · as Line of Sight (telecom): 2". A wrong guess then costs nothing.

   **Saved meanings** are kept in `knowledge/topics/` and learned from what the user does: downloading one group, a follow-up like "only the banking ones", or a correction. A correction always wins.

### 5.5 User memory (learned, not configured)

The harness builds a short memory about each user from what they do. Nobody writes it by hand; it replaces the idea of a hand-written deployment profile.

**What it holds:** short facts, each with its evidence and date. For example:

```json
[
  {"fact": "Interested in bank, NBFC and financial-regulator IT tenders", "evidence": "kept 11 of 11 bids from such buyers; quarantined all non-financial results", "source": "learned", "since": "2026-11-18"},
  {"fact": "LOS = Loan Origination System", "evidence": "downloaded the banking group twice; ignored Line of Sight", "source": "learned", "since": "2026-11-19"},
  {"fact": "We sell AML and LOS software to banks", "evidence": "user said so on 2026-11-20", "source": "stated", "since": "2026-11-20"}
]
```

**How facts get in:**
- **Stated facts are saved at once.** If the user says something about themselves ("we're a banking software company", "ignore state-government tenders"), it's saved straight away, marked `stated`.
- **Learned facts need repeated evidence.** (The memory step is built but not called yet; see 14.1.) After each run, a memory step (one short model call) reads the run log: what was asked, which bids the user opened, downloaded or restored from quarantine, and any corrections. It then proposes adding, updating or removing facts. A learned fact is saved only when at least 2 separate runs support it, and it is marked `learned`.
- **Contradictions update or remove a fact.** If the user later keeps a medical AML tender, the "only financial" fact is weakened, then removed.

**How facts are used:**
- The memory goes into the Chat agent's prompt: about 20 facts at most, newest and strongest first.
- It is one input when choosing a meaning (5.3), and the Searcher uses it to rank. For example, buyers the user keeps go higher.
- It never blocks anything. When memory and the request disagree, the request wins.

**User control:**
- "What do you remember about me?" lists the facts with their evidence.
- "Forget that" or "that's wrong" removes or corrects a fact.

**Ruling facts (18 Nov).** A user's call on a borderline bid (4.1) is saved at once as a memory fact of a separate kind, `ruling`, marked as coming from the click (evidence: "you ruled on GEM/2026/B/7914442 on 18 Nov"). The two-run rule does not apply.
- **Per topic.** Each ruling fact records the topic's short name (`chat.short_name`, e.g. "Loan Origination System") and is matched on that name, not the full topic key, so a reworded definition of the same topic still matches. A ruling never carries over to another topic and never to another user.
- **Active at once.** The fact is live as soon as it is saved. A small rulings agent (`agents/rulings.py`) runs after the Judge for each borderline bid, and again at each click for the run's other unruled borderline bids. It reads the topic, the bid's question and reason, and that topic's ruling facts only, and says which fact, if any, decides the question and which way. The bid's outcome then carries a ruling `{decision: counts | not, by: you | earlier, fact_id}`. Facts are read again for each bid, so a bid judged after a click is settled by it. The memory step never changes a ruling fact; only the user does.
- **Kept out of the Chat agent's prompt.** Ruling facts are left out of `prompt_facts` and do not count toward its 20 facts. A burst of rulings would push business facts out, and the Chat agent could fold a ruling into the topic definition, which would change the topic key and make the Judge judge again with a topic shaped by one user.
- **The newest wins.** If a new ruling answers a question an existing ruling fact answered the other way, the old fact is removed (evidence: "replaced by your ruling on … on …"), so the rulings agent never sees two facts that disagree. Bids the old fact settled in earlier runs keep their state.
- **Undo.** `DELETE /api/runs/{run_id}/rule/{b_id}` clears that bid's ruling. If the click created the fact, the fact is removed and the bids it settled in this run go back to Borderline; if the click only reused an existing fact, the fact stays. A bid the user has undone is never settled automatically again in that run.
- **Order of rules** (`bid_state` in `graph/views.py`; the first that applies wins): restored → relevant; a ruling → relevant if "counts", quarantined if "not"; borderline verdict with no ruling → borderline; otherwise the verdict's `relevant`.

**Starting empty:** a new user's first requests rely on the request itself and the site probe. Where meanings remain ambiguous, results are grouped (5.3). The memory fills in after a few runs, and the grouping then becomes rare.

Memory is per user for now. A shared team memory (facts true for everyone at the company) could be added later by promoting facts several users share.

3. **Word search matches whole words, and short acronyms only in their written case.**
   - Text is split into words, so "LOS" never matches inside "LOST", "CLOSE" or "LOSS".
   - Acronyms of 4 letters or fewer must match in their written case.
   - Phrases match in any case.

   Semantic search runs on the expanded `meaning` and phrases, never on the bare acronym.

4. **The Judge decides on meaning.** It gets the full definition, including `not_this` and `related_but_not_enough`, and must quote the passage that shows the meaning. A hit like "loss of service (LOS) penalty" is rejected.

### 5.4 How the Judge reads documents

Retrieval runs within each bid; there is no shared RAG store. A 112-page RFP doesn't fit in a ~32k-token prompt, and the first pages often miss the scope of work.

Per bid:
1. **Text.** Placeholder files are skipped, and pages with almost no text get OCR.
2. **Triage (cheap).** The model sees the title, buyer, category and bid PDF pages 1–2 (≈1.5k tokens) and answers *clearly no / maybe / clearly yes*. Clear negatives stop here.
3. **Find passages.** Chunks are about one page each. A word search (BM25, rules as in 5.3) and a semantic search (all-MiniLM-L6-v2 in-process, or a remote embedder, per 3.5.1) run over them, and the two rankings are merged with reciprocal rank fusion. The top ~8 chunks reach the verdict prompt. A reranker is wired but off by default: it only reorders the fused 24 and lost top-8 recall in the 20 Nov A/B (3.5.1).
4. **Judge.** The model decides on ≈10k tokens: the top chunks plus bid PDF pages 1–2 plus the RFP's scope-of-work / eligibility section. The decision is three-way (18 Nov): `relevant`, `borderline` or `not_relevant`.
   - **Borderline** means the documents show something close to the topic, and whether it counts depends on the buyer's purpose or the user's line of business, not on missing evidence. The Judge must then state the one question the user has to answer, naming the specific thing in doubt (at most 200 characters; empty for the other two decisions). If the passages simply do not settle the matter, the low-confidence path below still applies.
   - `Verdict.relevant` is true only for "relevant". `Verdict.borderline` and `Verdict.question` are stored beside it; older verdicts have neither and load as plain yes/no verdicts.
   - Quote rules are unchanged: 1–3 verified quotes, which for a borderline bid should show the part that makes it borderline.
   - The Judge is user-neutral and never sees user memory, so the shared verdict cache keeps working. Rulings are applied after judging, per user (5.5), and live on the run's outcome, never in the verdict cache.
   - Triage cannot say borderline: it reads too little text to raise a precise question.
5. **Read more, only for low-confidence bids.** (The deep read runs first; a verdict still borderline after it goes to the user.) The Judge may ask for more pages (`read_pages`) or try a different query, within a budget. As a last resort it reads the whole RFP in steps: a note per ~20 pages, then a decision.

Chunks and embeddings are saved with the cached documents (6.3), so a store across runs (Chroma or Qdrant, both installed) can be added later without re-processing.

**As built (17 Nov), while vLLM holds the GPU:**
- On CPU, bge-m3 embeds page-sized chunks at ~2.5 pages/s (a 112-page RFP ≈ 45 s), and the reranker takes ~9 s per 24 pages. Both break the 3–10 s per bid budget.
- So on CPU the Judge's meaning search uses **all-MiniLM-L6-v2** (`judge_cpu_embed_model`, max 256 tokens per chunk), merged with BM25 word search (5.3 rules, over the topic's expanded terms and acronyms). bge-m3 is not loaded by the app; the 20 Nov A/B (3.5.1) made MiniLM the default for both the local and the remote-off path.
- Measured on 18 judged bids, counting how often the quoted page lands in the Judge's top 8:

  | Ranking | Found | Time |
  |---|---|---|
  | words + MiniLM | 12/18 | 6 s |
  | words + bge-m3 on CPU | 10/18 | 423 s |
  | words only (16 Nov build) | 8/18 | — |

- The reranker is not used; the verdict call itself weighs the passages.
- After the matched passages, the Judge also gets the scope-of-work / eligibility passages and the **opening page of every attachment not already shown**. Before 17 Nov an attachment with no word match was never seen, and the Judge decided from the bid document alone.

**Instructions that came from wrong verdicts (17 Nov):**
- Quote what the documents say is bought, whichever way the verdict goes. Never quote the "Tender title", "Bid number" or "Buyer" lines of our own summary. The reason must say what the tender actually buys.
- GeM's "Similar Category" list on page 1 of the bid document is generated by GeM, not written by the buyer, so it is never evidence (in triage or judging).
- A topic named only as a possible future extension is not in scope; in scope means bought now.

**Quotes are checked** (`agents/evidence.py`):
- A quote must be found on the cited page: exact after normalising case and spacing, or ≥85% matched.
- A quote under 4 words is too short to count.
- A quote stitched from parts with "…", "...", "[...]" or " / " passes only if every part is found, in any order.
- A quote not found on its cited page is looked for in the rest of that file, then in the bid's other files. If found, the file and page are corrected.
- A verdict whose quotes all fail, or that is proven only by the title, goes back once with the failed quotes. This applies to rejections too.
- A relevant verdict that still fails is kept, marked `needs_review`, with confidence capped at 0.5. Rejections are never flagged.

The schema requires 1–3 quotes on every verdict (a "no" quotes what the tender is actually about) and the key-facts object, because the enforced JSON schema otherwise lets the model drop them. Live test: GEM/2026/B/8162911 (a bank's consultancy RFP) judged relevant for "management consultancy for banks" and not relevant for LOS, ~3.5 s per verdict.

**Confidence is not shown.** It is the model's own number, almost always ≥0.85; a triage "clearly no" is stored as 0.9. The UI shows the proof label instead (4.1): `proof()` in `agents/judge.py` returns documents, title or none.

## 6. Data access

### 6.1 Clients

- **HTTP first.** The built-in tools use one plain HTTP client per GeM host (`bidplus`, `fulfilment`, `admin`). Each has a cookie jar, keep-alive connections and its own adaptive rate limiter (1 s floor, 4 s after errors; 3.2). Hosts are fetched in parallel; requests to the same host are never parallel.
- **Session upkeep:** the client opens the page, reads the CSRF token (it is embedded in page JavaScript as `csrf_bd_gem_nk': '…'`), and refreshes it when a response says the session has expired.
- **Error handling:**
  - 404 + "No data found" means an empty result.
  - Other errors (any 5xx, 429, or a 404 without that body) get exponential back-off and 3 retries, with a fresh session on the last retry.
- **Browser second (not built yet; `tools/browser.py` is a stub).** A single Playwright Chromium session is the fallback, used when HTTP stops working (e.g. a new JavaScript challenge or a token generated in the browser). It also hosts the Monty `browser` module (7.3).

### 6.2 Live search (decided 18 Nov; replaces the local bid index; built 19 Nov 2026)

**Why.** The local index of all open bids (below, retired) needed a sync every 15 minutes and a nightly full re-fetch. On the shared server they ran nonstop and kept the shared spinning disk busy. The 18 Nov tests (end of this section) showed that searching GeM live when the user asks loses almost nothing against the index: across 13 topics, the index found 2 relevant bids that the live search missed, and both are caught by the base searches below. So each request searches GeM live, and nothing runs while nobody is searching.

**Assumptions.**
- **The machine may not be always on.** No background job is built. The old "always on" assumption (16 Nov) no longer holds.
- **Mostly one user at a time.** All GeM traffic shares the existing serialised, rate-limited queue (6.1, ≥1 s). No fair-share scheduling.

**What a request runs.**
- **Base searches,** always, whatever the plan says. The Planner chose none of them for 4 of 5 banking classes (5.2).

  | Kind | Value | Why |
  |---|---|---|
  | category | `services_home_itse_hi12433188` ("Hiring of Agency for IT Projects- Milestone Basis") | vague-title bank IT bids (171 bids, 18 pages) |
  | `exact` | `Department of Financial Services` | public sector banks and other Ministry of Finance bodies (~475 bids) |
  | `exact` | `BANKING SECTOR` | bank bids filed under this department instead (54 bids; case-sensitive) |
  | `fullText` | `consultants` | "Hiring of Consultants – …" advisory bids. GeM has no single category for these: each bid has its own (200 bids). |

- **The plan's searches,** after the base searches:
  1. its categories
  2. its ministries and departments, in `exact` mode
  3. its terms, in `fullText`
- **How searches are read:**
  - Every search is read to its last page. A page cap loses relevant bids (3.2).
  - A `fullText` search matching more than 20,000 bids is skipped, because it is nearly the whole list.
  - A search whose page 1 equals an earlier search's page 1 (same count, same bids) is read once. GeM matches multi-word queries mainly on the first word (3.2).
- **Two stages.**
  - **Stage 1** reads page 1 of every search. Its shortlist is triaged and judged, and results stream.
  - **Stage 2** then reads the remaining pages, and only the bids not yet seen are judged.

  So the first verdict does not wait for a broad search.
- **The date window** is applied locally. GeM filters closing dates only, and rejects half-open ranges.
- **The title screen** is unchanged (5.2):
  - A vague title without a card is never dropped; triage reads its documents.
  - A vague title with a card is screened by the card.
  - The screen is not stable between runs (6.2 tests), so it only drops titles that are plainly about something else.

**What is stored.**
- **Base search results:** cached for 1 hour, one small JSON file per search, written atomically.
- **Bid cache** (`data/bids/bids.sqlite`):
  - It holds the listing rows and cards of bids the Searcher shortlisted. It does not hold every open bid.
  - A row is written only when it is new or has changed. The index's habit of rewriting unchanged rows caused much of its disk load.
  - Rows are pruned 30 days after the bid closes.
  - On switching over, the index's cards (about 250) and the labelled set's bids are copied in once.
- **Cards** (format and writer as before, 6.2 below): written after a run for the vague-title bids that run collected, from the documents already in the cache, so no extra GeM requests. Cards serve every topic, since they describe what a bid buys.
- **Document and verdict caches:** unchanged (6.3).

**Understanding a request (5.3).**
- **Checking each meaning:** page 1 of up to 3 of its terms replaces the index probe. The count and top titles feed the choice between meanings.
- **A general class stays whole.** When a request names a general class ("payment systems", "treasury", "core banking"), the meaning covers the whole class, with its specific products as terms. On 18 Nov "Payment systems" was narrowed to "Payment Gateway".
- **Topics are not fixed definitions:** users can ask for anything.

**Speed** (measured on 18 Nov, one user):
- **Bank IT classes:** the full candidate pool in about 1.5–3 minutes.
- **Broad topics (AI, Payments):** about 5–6 minutes.
- **The first verdict:** after stage 1, in about 30–60 s.
- **Broad topics run longer than the 5-minute target in section 13,** while results stream. The user accepted this on 18 Nov, and broad terms are not narrowed to meet the target.

**Logging and disk.**
- httpx logs at WARNING, not one line per GeM request.
- No growing file is rewritten whole.
- Nothing runs in the background.

**Retired.**
- `gem-agent index` (load, sync, cards, embed) and `deploy/systemd/` (both timers)
- the index's sync, query and probe code
- the title embeddings of all open bids

The old `data/index/bids.sqlite` (about 350 MB) is deleted by hand once the cards are migrated.

**Not built (option for an always-on machine).** A nightly off-hours job could read the IT Projects category, about 18 pages, and write cards for that day's new vague-title bids, about 10–20. It would run only while `/proc/pressure/io` is under 20%. That would make the first search of the day faster for vague bank tenders.

#### Retired: the local bid index (16–18 Nov)

The rest of this section records the index as it was built, and the 18 Nov measurements behind the switch.

"Published in the last 2 weeks" covers about 36k bids. Paging through them live takes 3,600 requests, about 1¼ hours even at 1 s. So the system keeps its own index of all open bids and answers queries from it.

| Job | Requests | Time at 1 s gap (~1.3 s per request) |
|---|---|---|
| First full load of all open bids | ~4,400 pages | ~1.6 h, once |
| Keep up to date | ~260 pages/day (newest first; stop at the first page whose bids are all known and include one published before the last sync less 30 min) | ~6 min/day; run every 15 min (~3 pages, ~4 s) |
| Closing-date changes | corrigenda show up in `final_end_date_sort`. Re-fetch the full listing nightly (~1.6 h), and check `public-bid-other-details` for any bid at the moment it is shortlisted. | — |
| Closed bids | marked closed locally once past their closing date | 0 |
| Vanished bids (cancelled) | missing from **2** nightly re-fetches in a row → marked gone. One miss isn't trusted: a 1.6 h scan of a shifting list can skip a few bids. | 0 |

- **Where it runs:** on this machine, which is always on (confirmed 16 Nov). Two systemd user timers (`deploy/systemd/`, installed in `~/.config/systemd/user/`) handle it, and both catch up after a restart (`Persistent=true`):
  - `gem-index-sync.timer` runs `gem-agent index sync` every 15 minutes.
  - `gem-index-refresh.timer` runs `gem-agent index load` (the nightly re-fetch) at **01:37**. It was moved from 01:30 on 17 Nov because a sync starting at 01:30 held the lock.
- **Storage:** `data/index/bids.sqlite` holds three tables:
  - `bids`: the listing fields, plus `raw`, `embed_text`, `card`, `state` (open / closed / gone), `missed_refreshes`, `first_seen` and `last_seen`. On 17 Nov it held 41,657 open and 5,145 closed bids.
  - `embeddings`: bge-m3 vectors, stored as float16.
  - `sync_state`.
- **Why not "stop at the first known bid":** the newest-first list opens with reverse auctions whose start date is in the future (3.1). They are already known on the next sync, so that rule would stop on page 1 and miss everything new below them.
- **One job at a time:** the sync and the nightly re-fetch share a lock; a sync that finds the re-fetch running skips its turn, so the two never send to GeM side by side. The full load keeps a page checkpoint and resumes after a stop.
- **No documents are downloaded by the index.** It holds listing data only. Documents are fetched in two cases: when a bid is shortlisted for a request, or when the nightly pre-fetch picks it for a saved topic (6.3).
- **What the index stores:** SQLite holds every field in 3.1 for every bid, plus a bge-m3 embedding of its title / category / buyer. Embedding runs on the GPU when there is room, else CPU (3.5): about 2.6k new titles a day; the first load is a one-off batch.
- **A query is a local operation, done in seconds:** filter by date window, buyer and bid type; rank by semantic similarity to the topic `meaning` and by word matches; rerank the top ~200 with bge-reranker; shortlist.
- **Freshness:** GeM says new bids take up to 15 min to appear. The index is at most 15 min behind; for "today" requests the Searcher pages the newest-first list live until it meets the index.
- **Category searches** (5.2) run live and are merged in, for anything the title ranking might miss.
- **Bid cards for vague titles (decided 16 Nov, from measurement).** 236 of 41,232 open bids (0.6%) have titles that are a bare service category ("Hiring of Agency for IT Projects- Milestone basis", or a custom bid with nothing after the dash). A survey of 233 of them (the model's own judgement of the bid document, later disproved: the first backfill wrote 229 cards that only restated the title) found that they hid **the only open targets of the flagship topics**: two digital lending suites (LOS-type, Ministry of Finance / DFS), an Enterprise Fraud Risk Management Solution and a "Comprehensive Financial Risk and Compliance" platform. Word and meaning search could not reach them (meaning ranks 199, 219 and 1,886 for LOS; none for AML). So each vague-title bid gets a **card**: one line of what it buys plus up to 8 terms naming the systems or modules in scope ("anti-money laundering (AML)", "transaction monitoring"), never category words. The model writes it in one call from the bid document, an RA's original bid, and up to three RFPs: each RFP's first pages, its scope-of-work sections and its title-like lines, with every file name of identical uploads. The attachments are always collected (4–6 requests once per bid, into the document cache the Judge reuses). The bid document alone is not enough: for 10142481 it named only "Comprehensive Financial Risk and Compliance Solutions", and only the RFP's scope of work said AML. `gem-agent index cards --redo` rebuilds every card after the writer changes. The card joins the indexed text (word search and embedding); the Searcher's title screen sees it. The sync writes up to 40 cards per run (~10–20 new vague bids a day); `gem-agent index cards` backfills. A vague bid without a card is still never dropped.

**What the index cost in practice (18 Nov; the timers are now off).**

- **Each 15-minute sync read 100–120 pages, not the planned ~3.** The `sync_recent` stop rule does not stop where 6.2 intends. Each sync then rewrote every row it read, raw JSON included, even when nothing had changed.
- **Syncs ran end to end.** Each took 20–45 minutes of wall time but only 15–30 s of CPU, so it was waiting on the disk almost all the time. The next timer fired as soon as one finished.
  - The card step took 6–22 minutes even when it wrote no cards.
  - Embedding 75–274 new titles took 10–18 minutes, against about 10 s per 256 titles at 2 am.
  - Each run was a fresh `uv run` process that loaded bge-m3 from disk again.
- **The shared disk.** `/` is one LVM volume across the NVMe drive and the shared spinning disk (`sda`). A file's blocks can land on either, and `bids.sqlite` lies between 45 GB and 2,418 GB of the volume.
  - Random reads of `bids.sqlite` ran at about 200 KB/s while the disk was busy; a sequential copy of the same file took 19 s.
  - At idle I/O priority, loading bge-m3 took 16 minutes.
- **Shared-server notice (18 Nov):**
  - Run indexers and batch jobs hourly or off-hours, not every few minutes.
  - Post before re-indexing.
  - Keep logs at INFO or WARN. httpx logs every GeM request at INFO, so a full load writes about 4,400 journal lines.
  - Retries must back off.
  - `/proc/pressure/io` (avg10) above about 20% means the disk is struggling. It stayed at 30–66% through the evening of 18 Nov.

**Live search compared with the index (bias test, 18 Nov; `eval/live_search/`).**

- **The method.** For each of the 8 labelled topics:
  - **Live pool:** every page of the topic's term searches plus the IT Projects category.
  - **Index pool:** the Searcher's index pool, with no date window.
  - Bids found by only one side were title-screened with the Searcher's screen. Claude labelled the plausible live-only bids from their documents: all of them for AML, fraud, KYC, LOS and RBI, and 20 each for AI, consulting and Ministry of Finance.
- **The results:**
  - **No undercount for most topics.** The live search found no relevant AML, fraud, KYC, LOS, RBI or consulting bid that the index missed.
  - **AI undercounts slightly.** 1 relevant and 5 unsure of 20 sampled, out of 131 plausible.
  - **Few index-only bids pass the title screen:** 1–11 per topic.
- **The Ministry of Finance row** (19 of 20 relevant) only shows that the department searches find the ministry's bids. The test's index pool had no buyer filter, and the real Searcher has one.
- **Relevant bids found by neither search** cannot be measured this way.

**Five of the business's untested tender classes (18 Nov; `eval/live_search/results/2026-11-18-classes/`).**

- **The method.** Each class phrase went through the app's own Chat and Planner steps, with scratch memory. The live pool was then built the way a live-search design would run it:
  - **Categories:** the plan's, plus IT Projects.
  - **Buyers in `exact` mode:** the plan's, plus Department of Financial Services.
  - **Terms in `fullText`:** every one read to the end; searches over 20,000 bids skipped (none was); repeated searches read once.

  The index pool was the Searcher's pool for the same plan. Both were title-screened. Claude labelled every "both" and "index only" bid, plus 20 live-only bids per class, from the documents (148 pairs over 102 bids). The labellers were not told how each bid was found.
- **The Payments definition.** The Chat step narrowed "Payment systems" to "Payment Gateway". The user widened it to payment systems in general: switches, UPI/IMPS/AEPS/BBPS, RTGS/NEFT, card management, ATM/POS switching. The plan and pools were then redone.

| Class | Live pool | Index pool | Time to read the full live pool | Both: relevant | Index only: relevant | Live only: relevant (sampled) |
|---|---|---|---|---|---|---|
| Treasury (incl. Murex) | 986 | 63 | 113 s | 0 of 6 | 0 of 2 | 0 of 20 (of 48) |
| Core Banking | 1,169 | 64 | 123 s | 1 of 3 | 0 of 3 | 0 of 20 (of 40) |
| Payments (widened) | 1,555 | 89 | 351 s | 5 of 14 | 0 of 3 | 1 of 20 (of 56) |
| Corporate Internet Banking | 704 | 64 | 78 s | 0 of 4 | 0 of 2 | 0 of 20 (of 36) |
| M&A Advisory | 661 | 63 | 79 s | 0 of 3 | **1 of 8** | 1 of 20 (all) |

- **What the live search missed: 1 bid in 17 index-only.** Mazagon Dock's "Hiring of Consultants … Transaction cum Legal Advisory" was not reached by any search in the live pool. Its category is unique to the bid, so the fix is the `fullText` "consultants" base search (6.2).
- **What the live search added:** about 3 relevant Payments bids and 1 M&A bid that the index did not reach.
- **Few relevant tenders are open for these classes now.** Treasury (Murex included) and Corporate Internet Banking have none.
- **Two of the five relevant Payments bids have vague titles.** Both are "Hiring of Agency for IT Projects" (Indian Bank, IOB), reached only through the IT Projects category and the Department of Financial Services.
- **The Planner** chose no categories for 4 of the 5 classes and no buyers for any of them. The 3 payment categories it chose had 0 open bids.
- **The title screen is not stable.** Re-screening the same pools gave Core Banking 15 plausible "both" bids instead of 3.
- **Times** include about 65 s for the two shared base searches. Single-page searches took about 4 s at 22:30, against about 1 s earlier.
- **Labelling cost** about 1.8M tokens for 21 labellers of 5 bids each, roughly 65k fixed per labeller plus about 5k per bid.

### 6.3 Document and verdict cache

- **Documents** are stored by `b_id` and SHA-256 under `cache/docs/<b_id>/`, with their text, OCR, chunks and embeddings. They are shared across runs and users and fetched again only when a corrigendum appears.
- **File names (16 Nov).** GeM names uploads by number ("1793961738.pdf"), and buyers often upload one RFP into every slot: 10115089 listed nine files, five of them one RFP. Each file gets, once, with no model call and no request (`cache/names.py`): the slot label the bid document gives it ("Scope of Work:1793964442.pdf"), its own heading (on page 1, the earliest of: a subject line ("Sub: …"), an annexure / appendix / schedule marker, or the first line that names a kind of document. A heading runs over several lines until a blank line, a subject line, a salutation, a bracketed instruction ("(To be filled…"), a new all-caps heading, or a line ending in ".", ":" or ","), or GeM's fixed page it is (`bidsla` → service level agreement). Files with the same text are shown once, with the other labels and file names they were uploaded under. The label names a file unless copies disagree or it says nothing ("Additional Document 1"), then the heading does, and the file name is the last resort. Different texts under one heading are numbered versions: 10115089's two RFP versions score technical evaluation out of 300 and out of 100. Quotes name the document they come from, and downloads are saved as "GEM-2026-B-8229813 - Scope of Work.pdf".
- **Verdicts** are keyed by (`b_id`, topic key, document hash). The same question about the same bid is never judged twice. Changing the Judge's instructions does not change the key, so re-judging after an instruction change means judging into a separate verdict store and copying the results across.
- **Topics** that users ask for repeatedly (from `knowledge/topics/`) get a nightly pre-fetch (not built yet). Documents for newly published bids that rank high for those topics are downloaded in the background, so a morning query finds them ready.

## 7. The harness: tools, skills and the sandbox

**Not built yet (17 Nov).** `gem_agent/harness/` has the layout below, but each module is a one-line stub. Today the built-in tools are the only rung of the ladder, and a failing tool fails that item. The design below is unchanged.

| Part | What it is |
|---|---|
| **Capabilities** | Named steps with typed inputs and outputs, e.g. `gem.search(category, pages) -> list[Candidate]`. Agents ask for a capability, not a specific tool. |
| **Tools** | Our built-in code for a capability (section 6). |
| **Skills** | Code the model wrote that passed its check and the fresh-session replay; saved and reused (7.4). |
| **Sandbox** | Monty plus host modules (7.3). New skills are written there and all skills run there. |
| **Checks** | Code we write, one per capability, that decides whether a result is real (7.4). |
| **Memory** | Run logs, user memory (5.5), saved topic meanings, skill records. |

These parts know nothing about GeM, so CPPP and bank sites will reuse them unchanged.

### 7.1 The ladder

For each capability the system tries, in order:

1. **The built-in tool:** HTTP, with the browser as its fallback (section 6).
2. **Skills** for that capability, best track record first. These need no model calls.
3. **Discovery:** the model writes new code in Monty (7.2). Up to 3 attempts of up to 25 turns each.
4. **Give up on that item:** it is marked failed with the reason and reported. The run continues.

Every result is checked before it's accepted, whichever rung produced it. A tool that returns nonsense counts as a failure.

### 7.2 Discovery loop

- **Start:** the model gets the capability's goal, its input and output types, the check's rules, and the host API. It does **not** get endpoint names or old code.
- **Each turn:** the model sends one code block. The harness runs it and returns the printed output, the result, or the error (all trimmed).
- **Stuck detection:** if the same call (or the same failing pattern) repeats 3 times, or 8 turns pass with no new information, the harness tells it so and suggests the tool it hasn't used (e.g. "you haven't read `browser.network()` since clicking Search"). This is the failure in run 4b.
- **When the check passes:** the code goes on to skill creation (7.4).

### 7.3 Monty sandbox and host modules

Monty (`pydantic-monty` 1.6) runs plain Python only. There is no `urllib`, `socket`, `httpx`, `subprocess` or `html.parser`, and no file access. All input and output goes through host modules:

| Module | Functions |
|---|---|
| `browser` | `goto(url)`, `forms()` (visible controls with labels and option counts), `options(select_id, text)`, `set_value(selector, value)`, `click(selector_or_text)`, `network(last)` (the page's own requests with post data and response heads), `eval_js(code)`, `snapshot()` (accessibility tree). Withheld by default and offered after 10 turns without progress: `html()`, `find(regex)`. |
| `http` | `get(url)`, `post(url, data)` through the same per-host clients and limits as the built-in tools |
| `files` | `save(name, bytes)`, `read(name)`, limited to the run folder |
| `pdf` | `text(name, pages)`, `links(name)` |

Limits:
- Every call passes through the host allowlist (`*.gem.gov.in` for now) and the per-host rate limit.
- Monty `ResourceLimits` cap time, memory and host calls per attempt.

### 7.4 Skills

A passing discovery becomes a skill in four steps:

1. **Generalise.** The model rewrites the code as a function with the capability's inputs (e.g. `run(category, pages)`). It must take every value tied to one session (tokens, cookies, ids) from the page at runtime.
2. **Scan.** The harness rejects the skill if a value seen during discovery appears as a literal in the code: a CSRF token, cookie or session id. In run 4a, the literal `csrf_bd_gem_nk` value would have been caught here.
3. **Replay.** The skill runs in a **fresh session on different inputs** (another category, other dates) and must pass the check again. If it fails, it goes to a repair loop: the error goes back to the model, which may probe the page and redefine the function, up to 6 rounds.
4. **Save.** The skill is saved to `skills/<capability>/<id>.py` with a record:

```json
{
  "capability": "gem.search",
  "description": "Search GeM by category via advance-search: read token from page JS, POST search-bids per page",
  "inputs": {"category": "str", "pages": "int"},
  "check": "gem.search",
  "created": "2026-11-16T09:20:00Z",
  "created_from_error": "built-in gem.search: token not found after 3 retries",
  "replayed_on": ["home_info_soft_secu_cy58162858 (0 rows, confirmed No data found)"],
  "runs": 1, "passes": 1, "last_pass": "2026-11-16T09:21:00Z", "last_fail": null
}
```

**Checks** are written by us, per capability:
- **`gem.search`:** every row has a valid bid number (`GEM/YYYY/B|R/n`) and a numeric `b_id`, with no duplicates. An empty result counts only if GeM said "No data found". A non-empty result needs its count to match the reported `numFound` page arithmetic.
- **`gem.download`:** a valid PDF or archive, not 0 bytes (the RA case: `showbidDocument` returns an empty PDF). HTML passes only for routes that serve documents as HTML (`bidsla`, `showCatalogue`, 3.3); anywhere else it counts as an error page.
- **`gem.attachment_links`:** every link is on an allowed host.

**Managing skills:**
- **Order:** skills are tried by pass rate, then by most recent pass.
- **Retirement:** a skill that fails 3 runs in a row is retired, not deleted.
- **Promotion:** a skill that keeps working while the built-in tool keeps failing is flagged, so a developer can make it the built-in tool.

### 7.5 Deferred: container sandbox for new libraries

If a capability ever needs a library Monty can't provide, the plan is a throwaway Docker container:
- It may install packages.
- It may only reach the allowed hosts.
- It may only write to one output folder.
- It is deleted after each attempt.

It would slot in as rung 3b. It is not built now (decided 13 Nov).

## 8. Time plan

Goal: first results in minutes, not hours.

| Step | How it's kept fast | Typical time |
|---|---|---|
| Understanding + plan | 1–2 model calls; saved topic meanings skip the expansion | ~10 s |
| Finding candidates | live searches in two stages (6.2): page 1 of every search, then the rest | stage 1 ~20–40 s; full pool ~1.5–6 min |
| Fresh corrigendum check for shortlisted bids | 1 cheap request per bid | ~1.3 s × n |
| Downloads | from the cache when present; else 1 bid PDF + ~2–3 attachments per bid, hosts in parallel, **closing-soonest first** | ~4–5 s per uncached bid |
| Judging | runs alongside downloads; triage drops most candidates in one short call | ~3–10 s per bid |
| Results | streamed per bid as judged | first result in under 1 min |

Example: 40 shortlisted bids, none cached. About 160 requests spread over 3 hosts takes **~2–3 min** in total, with the first result in under a minute. With live search (6.2), add the search time: the full pool takes about 1.5–3 min for bank IT classes and 5–6 min for broad topics, while results stream from stage 1.

Further gains, if needed:
- **Background pre-fetch** of documents for saved topics (6.3).

## 9. Compute plan

The model is the scarce resource, since it's shared. It should only see what cheap steps couldn't settle.

| Stage | Who does it | Rough cost per request (40 candidates) |
|---|---|---|
| Shortlist from 36k bids | local embeddings + reranker (GPU or CPU, 3.5) | 0 model calls |
| Triage | model, ≈1.5k tokens per bid, short answer | 40 × 1.5k = 60k prompt tokens |
| Judge (survivors only, ~10) | model, ≈10k tokens per bid | ~100k prompt tokens |
| Deep read (borderline, ~1–2) | model, page-by-page RFP read | ~60k prompt tokens |
| **Total** | | **≈ 220k prompt tokens.** Reading every candidate's full documents would be ≈ 1.2M. |

Other savings:
- **Caching:** verdicts are cached by bid + documents + topic (6.3), and topic definitions are cached. Repeated questions cost nothing.
- **Prompt layout for prefix caching:** fixed instructions and the topic definition go first, the bid's evidence last. Consecutive calls then share a prefix the server can reuse.
- **Short outputs:** typed answers (JSON fields), with `max_tokens` capped per agent (triage 150, judge 700, deep-read notes 300; the Searcher's title screen 600, raised from 300 on 17 Nov after it cut off a long answer). Temperature 0.
- **Skills over discovery:** a skill replays with no model calls. Discovery (30–100k tokens) runs only when a tool and all skills have failed.
- **Parallel requests:** the Judge sends 2 model calls at a time (`JudgeSettings.parallel`); vLLM batches them (`--max-num-seqs 6`).
- **No second opinion (measured 17 Nov).** 144 bids from three AI runs were judged a second time with identical code, topics and documents:
  - Only 2 verdicts changed (1.4%; 2 of the 34 bids relevant in either run).
  - Both were triage "maybe" bids decided by the full Judge, and both were genuinely borderline (proctoring plugins; a Raspberry Pi driver-monitoring device).
  - Asking twice on "maybe" bids would add about 70 judge calls per 144 bids (median 8 s each) to catch about 2 changes.
  - Borderline cases are better settled in the topic meaning, and correctness is measured with the labelled set (13).

## 10. Gaps handled

| Gap | Handling |
|---|---|
| A wrong rejection loses a real tender | Quarantine for 30 days instead of deleting (R6). The user can ask "show rejected" and restore any of them. |
| Users want what's new, daily | (Not built yet.) Saved searches run on a schedule (e.g. 08:00 daily) against the index. They report only bids that are new or changed since the last run. |
| Closing dates move | Each shortlisted bid gets a fresh corrigendum check. (Not built yet:) Reported bids are re-checked daily, and the user is told when a closing date changes. |
| Scanned PDFs | OCR (Tesseract, English) for pages with almost no text (3.4) |
| Hindi text | Bilingual documents are read from the English text; garbled Hindi is ignored. bge-m3 handles any clean Hindi. Hindi OCR is out of scope for now. |
| Several users at once | One job queue. One client per GeM host, shared by all runs; the cache is shared. Each run's results and downloads are visible only to the user who started it. |
| Runs taking too long | Closing-soonest first, streamed results, index plus cache (section 8) |
| Placeholder attachments | Detected from their content and skipped (3.3) |
| Reverse auctions | Correct document route by bid type; the original bid is fetched through `b_id_parent` (3.3) |

## 11. Storage layout

```
gem_agent/
  app/            chat API + UI
  graph/          langgraph graph, RunState
  agents/         chat, planner, searcher, collector, judge, reporter
  tools/          http clients, browser fallback, gem tools, pdf/ocr tools
  index/          bid cache code (bids.sqlite reused without embeddings), cards; data/bids/bids.sqlite (6.2)
  sandbox/        Monty host modules, checks, discovery + repair loops
  skills/         skill code + records (7.4)
  knowledge/      users/<user>/memory.json (5.5), topics/ (saved meanings)
  cache/          lookups/ (categories, ministries…), docs/<b_id>/ (files, text, chunks, embeddings), verdicts/, gtc/
  runs/<run_id>/  request.json, plan.json, shortlist.json, log.jsonl, summary.md, bids.zip
                  bids/<bid_number>/ → links into cache/docs     quarantine/ (30 days)
```

### 11.1 Retention: what gets deleted, and when (confirmed 16 Nov)

Bid documents average **3.2 MB per bid**, measured over 50 bids. The bid PDF itself is only ~0.14 MB; the attachments (RFPs, scope documents) make up the other ~3 MB, plus about 0.5 MB of text, chunks and embeddings. Everything below is deleted automatically by a nightly clean-up job; the periods are settings. **The clean-up job is not built yet** (`jobs/retention.py` is a stub), so nothing is deleted today.

| What | Deleted when | Why |
|---|---|---|
| Documents of a **quarantined** bid (judged not relevant for that user) | 30 days after the rejection, unless another user kept the same bid | Time for the user to spot a wrong rejection and restore it |
| Documents fetched but **kept by no one** (dropped at triage, or pre-fetched and never used) | When the bid closes | No use once bidding has closed |
| Documents of a bid a user **kept** | 180 days after the bid's closing date | Covers bid preparation, clarifications and award follow-up |
| Text, OCR, chunks, embeddings of a bid | Together with its documents | Derived data |
| Zip files | 7 days after creation | Rebuilt on request from the cached files |
| Verdict cache entries | 30 days after the bid closes | Only useful while the bid can still be bid on |
| Detailed run logs (`log.jsonl`: every request, tool call, model call) | 90 days | For debugging; large |
| Bids in the bid cache (6.2) | 30 days after the bid closes | Only the bids shortlisted by searches; small |
| Base search results (6.2) | 1 hour | Re-read from the site after that |
| Lookup lists (categories, ministries…) | Replaced daily | Always refreshed from the site |

**Never deleted automatically:**
- **Run records:** request, what was understood, shortlist, every verdict with its evidence, the summary. A few KB each; they are the audit trail.
- **User memory** and **saved topic meanings.** Only the user removes these ("forget that").
- **Skills and skill records,** including retired skills.

**The user can delete by hand** (only single memory facts so far; 14.1)**:** a whole run (its record, and its documents unless another run uses them), single memory facts, or everything about themselves.

**Storage estimate:** about 100 new bids downloaded a day with ~10% kept gives roughly 15 GB at steady state for documents, plus about 1 GB for the index. The machine has 626 GB free.

## 12. Safety and politeness

- **Traffic:** ≥1 s between requests per GeM host (adaptive, 4 s after errors), one client per host, back-off on real errors, a daily request budget (not enforced yet; 14.1).
- **Downloads are untrusted:**
  - They are stored, never executed or imported.
  - PDFs are parsed with pypdf, pdftotext or PyMuPDF only.
  - Archives are listed before extraction; paths outside the folder are rejected.
  - Links are followed only to allowlisted hosts.
- **Sandbox:** no network or file access except through the host modules, which enforce the allowlist, rate limits and the run folder.
- **Model server:** only the loaded model is ever named (3.5).

## 13. Evaluation

**Status (17 Nov): built, awaiting review.**
- **The set:** `eval/labelled_set.json` holds 90 (topic, bid) pairs over 77 bids. It covers 8 topics: AML, fraud, KYC, LOS, AI platform, management consultancy for banks, central bank, and Ministry of Finance.
  - Each topic has likely matches, related-but-not-enough bids, look-alikes, and one random open bid.
  - Each topic is fixed to the topic definition saved from the user's run. LOS uses the definition in 5.3.
- **How it was labelled:** Claude labelled each pair from the bid's documents, never from the Judge's output, following the user's rulings.
  - Every label carries a verbatim quote, and every quote was checked against the cited page.
  - 32 pairs are relevant and 16 are marked unsure. 23 are flagged `to_review`: every unsure label plus a random 10% of the rest.
  - A reviewer's answer goes in `reviewed_by` and `review_note`, and overrides the label.
- **Running it:** `gem-agent eval [--topic t] [--out rows.json]` judges every pair with the current code into a scratch verdict store, then prints precision, recall, triage losses and each disagreement.
- **First run (17 Nov):**

  | | Result |
  |---|---|
  | Precision | 89% (25 / 28) |
  | Recall | 78% (25 / 32) |
  | Lost at triage | 4 |

  - 8 of the 10 disagreements are on unsure labels: AI applications bought with hardware, a KYC-monitoring tool under AML, and consultancy edge cases. The review settles these.
  - **Two disagreements are on sure labels:**
    - **Bank of India's KYC compliance monitoring unit:** triage dropped it as "general IT project agency hiring". It is a vague-title bid, and triage sees only the bid document, so it never saw the attachments.
    - **National Housing Bank's training-content agency:** the Judge called it management consultancy; the label says it is not.

**Relevance labels, recommended approach:**
1. I build a set of ~80 bids from the index: likely positives for AML and LOS (found by semantic search), look-alike traps (LOS as loss / Line of Sight / Level of Service, AML in medical tenders, CBS as Central Bureau of Statistics), "related but not enough" bids (LMS-only, plain core banking), and random negatives.
2. I label each one from its documents, with the quoted evidence, and mark the ones I'm unsure about.
3. You, or a colleague who knows banking-software sales, review only my unsure labels plus a random 10%. That keeps your time to roughly 15–20 bids.

**Measures:**
- **Judge:** precision and recall on that set; recall matters more.
- **Borderline (18 Nov):** `gem-agent eval` also reports the borderline rate (borderline verdicts out of all bids judged) and agreement with the unsure labels: of the borderline verdicts, how many are on unsure labels, and how many of the unsure labels were judged borderline. A rate far above the share of unsure labels (16 of 90) is a failure, because the user is back to reading everything. Every borderline question must name a specific thing in doubt (checked by reading them). Recall counts a borderline verdict on a relevant label as found, since it reaches the user; precision counts it as not relevant, since it is never shipped unasked. Borderline verdicts are left out of the disagreements and listed in their own "Borderline (question)" section with the label and the question.
- **Triage:** must not drop any bid the full Judge would accept (target 0 lost).
- **Topic expansion:** ~20 acronym requests. The right meaning must be picked with no question asked. Truly ambiguous requests must be searched both ways and grouped.
- **User memory:** replay a scripted sequence of runs (keep / download / quarantine actions). Facts must appear only after 2 supporting runs, stated facts must be saved at once, a contradicting run must weaken or remove a fact, and "forget that" must remove it.
- **Planner:** no invented site values (target 0).
- **Index:** the shortlist for a topic is compared with a slow live search on a sample day; recall of relevant bids should be ≥95%.
  - **Done for the pool, not the shortlist, on 18 Nov (6.2, bias test).** Compared with a live search, the index pool missed no relevant bid for 6 of the 8 topics and a few for AI. The comparison added 182 labels to `eval/labelled_set.json` with `bucket: "live_only"`, so the set held 272 items. Of the new labels, 20 are relevant, 11 unsure and 28 `to_review`. The 5-class test (6.2) then added 148 more labels and 5 topics: 9 relevant, 19 unsure, 32 `to_review`. That brings the set to 420 items over 13 topics.
- **Harness:** each built-in tool is disabled in turn. Discovery must produce a skill that passes the fresh-session replay, and the next run must use it with no model calls.
- **Time:** a 40-bid request must complete in under 5 min uncached, with the first result in under 1 min. With live search (6.2), broad topics may take 5–6 min for the full pool while results stream; this was accepted on 18 Nov.

## 14. Build order

1. **HTTP clients + GeM tools:** lookups, both search endpoints, document routes by bid type, attachments, corrigendum. Check against section 3.
2. **Local index:** first full load, 15-min sync, title embeddings, query and rerank.
3. **Collector + text** (placeholder detection, OCR) + document cache. Built 16 Nov: the Collector is plain code (no model). Per bid it costs 1 request when cached and unchanged (2 if the bid has a corrigendum, to compare its entries); an RA also fetches its original bid and that bid's attachments; archives are expanded in memory; chunks and embeddings come with the Judge (step 4).
4. **Judge** (triage + retrieval) on the labelled set. Judge built 16 Nov; the labelled set needs the completed index.
5. **Chat (topic expansion, probe, user memory read + post-run memory step) + Planner.** Built 16 Nov:
   - **No tool calls while understanding.** vLLM enforces a JSON schema on every answer, which rules out tool calls in the same request. So Chat makes one call that proposes 1–3 candidate meanings; code runs `probe_site` on each against the local index; a second short call weighs them only when more than one remains.
   - **The Planner can't invent values (R2).** The candidates (categories found for the topic, ministries in the index, departments matching the buyer hints) become enums in the answer's schema; the server enforces it, validation rejects anything else and the agent retries.
   - **Buyer organisation comes from the bid document, not the listing.** GeM's row carries only `Ministry/State Name` and `Department Name` (checked across all 44,207 raw rows on 16 Nov); the organisation ("Bank Of Maharashtra", "NaBFID") is on page 1 of the bid PDF. So the index holds ministry and department, buyer hints rank rather than filter (5.3), and the Judge sees the organisation from the collected bid document (`tools/gem/bid_fields.py`), which the results also show. Filtering by buyer type ("only cooperative banks") at index level would need the organisation for every open bid: one request each, ~40k requests ≈ 11 h once, plus ~45 min a day for new bids.

- **Buyer hints rank, they don't filter.** The index holds ministry and department, not the buyer organisation (a cooperative bank often sits under a state department), so a hard filter would silently drop real tenders. The Planner's ministries/departments become `prefer_*` for the Searcher's ranking; they are hard filters only when the user writes "only" or "exclusively". With no buyer hints, no ministries are offered at all.
   - **Every field of a model-facing schema is required.** With the server enforcing the schema, the model leaves out fields that have defaults (live: it set `last_days=14` but left the window kind at "open"; it dropped the Judge's quotes). A test checks all model-facing schemas. The user's own number of days wins over explicit dates the model also fills in.
   - Live (16 Nov): "LOS … last 2 weeks" → Loan Origination System, published 2–16 Nov, Line of Sight / Level of Service under `not_this`; "AML … closing in the next 10 days" → closing 16–26 Nov; "we're a banking software company" saved as a stated fact. ~6–10 s to understand, ~1.5 s to plan.
6. **langgraph graph** + command-line chat, streaming results. Built 16 Nov, with the FastAPI chat API (SSE) the web chat uses:
   - **Understanding happens before the graph.** The run manager needs the intent first: a new search starts a run; a refinement starts a follow-on run carrying the earlier requests (caches reuse its downloads and verdicts); "show rejected" and memory questions answer on the current run's stream (`show`, `message` events) without a run.
   - **Graph:** plan → search → (widen / narrow back to the Planner, at most 2 retries) → process → report, SQLite-checkpointed; a crashed run resumes from its last node (tested), and the app resumes interrupted runs on start.
   - **Searcher: word matches + closest-by-meaning titles, then one batched model call screens the titles.** Measured 16 Nov on the full index: the title-only reranker can't separate relevance (its top AML hit was "SOFTWARES") and costs 10–18 s per query on CPU, so it stays off on CPU. Vague titles ("Hiring of Agency for IT Projects- Milestone basis", empty custom bids) are never dropped by the screen: only their documents can tell.
   - **Two-stage collect:** the bid document first (1–2 requests) and triage on it; attachments only for bids that pass. A junk candidate costs ~2 requests instead of ~5.
   - **Sync stop rule fixed:** stop at the first page whose *newest* bid predates the last sync (less 30 min). The earlier "first all-known page" rule never stopped, because the full load skips ~2.7% of bids scattered through the list. Each sync still reads ~60 pages of reverse auctions GeM lists a day ahead (634 on 16 Nov) before reaching today's bids; acceptable (~6k requests/day of the 20k budget), to be reduced if `byType` can split bids from RAs.
   - **Checkpoint types (17 Nov).** LangGraph's checkpoint serialiser warns about, and will later refuse, rebuilding classes that are not on an allowlist. `graph/checkpoint.py` builds that allowlist from `RunState`'s field types (8 types: RunState, TenderRequest, TopicDefinition, ResolvedWindow, SearchPlan, Candidate, Bid, BidType). Any other class is not rebuilt.
7. **Harness:** Monty host modules, checks, discovery + stuck detection, generalise / scan / replay / repair, skill library. Tested by disabling built-in tools. **Not built yet.**
8. **Reporter, chat UI, scheduled searches, quarantine.** Built: the web chat (4.1), quarantine with one-click restore, and the zip download. The Reporter is only a closing count line (14.1). Not built: scheduled searches.

### 14.1 Gaps found on 17 Nov

Found on 17 Nov by checking the code against this spec. These parts are described above as working, but either nothing in the app calls them or they are one-line stubs.

| Gap | Spec | What the code does today |
|---|---|---|
| **The post-run memory step never runs** | 5.5 | `agents/memory_step.py` (`MemoryStep.after_run`) is built and tested, but nothing calls it after a run. Stated facts are saved; learned facts never appear. Restores are stored as `signal` events that nothing reads. |
| **No daily request budget** | 12 | `daily_request_budget` (20,000) is a setting no code reads. The per-host spacing and back-off do work. |
| **No browser fallback** | 6.1, 7.1 rung 1 | `tools/browser.py` is a stub. When an HTTP route fails, that bid fails. |
| **No detailed run log** | 5.5, 11, 11.1 | `memory/run_log.py` is a stub. `log.jsonl` (every request, tool call and model call) is not written; runs keep only their event stream. |
| **The Reporter is a count line** | 4 step 5, 5.2 | `agents/reporter.py` is a stub. The `report` node sends "Done: N relevant, M quarantined". It writes no `summary.md` and sends no summary table or quarantine note in the chat. The results panel and the zip work. |
| **Corrections mid-run start a new run** | 4 step 3, 5.1 | A message sent during a run does not re-plan that run. A refinement starts a follow-on run that reuses the cached documents and verdicts, and the first run carries on. |
| **No daily re-check of reported bids** | 10 | The corrigendum is checked when a bid is collected. No job re-checks reported bids or tells the user that a closing date moved. |
| **No deleting by hand** | 11.1 | There is no API to delete a run or "everything about me". Single memory facts can be deleted. |

### 14.2 Found on 18 Nov

| Problem | Spec | What happens |
|---|---|---|
| **The sync does not stop at the last sync** | 6.2 | `sync_recent` read 100–120 pages per 15-minute run instead of about 3, and rewrote every row it read. |
| **The scope-heading regex can run for minutes** | 6.2 (cards), 5.4 | `cards._SCOPE_HEADING` backtracks exponentially on long runs of digits. The numbering prefix `(\d+(\.\d+)*\.?\s*)*` can split the digits in astronomically many ways. An OCR'd line "3333883233833 322232333833333383333333333333333338" (bid 10110777) held one call for more than 13 minutes of CPU. This affects the card writer and anything else that calls `_scope_sections`. **Fixed on 18 Nov:** the numbering prefix is now atomic, `(?>\d+(?:\.\d+)*\.?|[ivx]+\.|[a-z][.)])`. It gives the same matches on 221,804 cached lines. Regression tests are in `tests/index/test_cards.py`. |

## 15. Open questions

- **18 Nov: the business's tender classes are mostly untested.** The user named 26 classes:
  - Murex, Treasury Management, Capital Markets Risk, AI Agents for Banking, Core Banking, Digital Banking Transformation, Payment Systems, API Integration, LOS, Lending Solutions
  - AML, Fraud Risk Management, Financial Crime Prevention, M&A Advisory, Islamic Banking, Forex, Derivative Trading Platforms, Corporate Internet Banking
  - Financial Software Development, Banking Technology, FinTech Solutions, Enterprise Application Implementation, System Integration Consulting, Implementation Support, AI platform, AI transformation

  The labelled set covers about 5 of them (AML, fraud, LOS, AI platform, and Financial Crime Prevention in part). Five more were tested on 18 Nov (6.2): Treasury/Murex, Core Banking, Payment Systems, Corporate Internet Banking, M&A Advisory. That leaves about 16 unmeasured. The 148 class labels and the 5 class topic definitions are merged into `eval/labelled_set.json`, with buckets `both`, `index_only` and `live_only`. Payments uses the widened definition. The set now holds 420 items over 13 topics.
- **18 Nov: index or live search: decided, live search** (6.2). Built 19 Nov 2026: the index sync, query and probe code and the systemd timers are removed; `gem-agent bids migrate` copies the old cards.
- **18 Nov: the Chat step can narrow a class.** It turned "Payment systems" into "Payment Gateway". Decided: the Chat step keeps a general class whole (6.2, 5.3), not saved fixed definitions, because users can ask for anything. Built 19 Nov 2026.

Waiting on review (17 Nov):

- **The labelled set's flagged labels (13).** 23 pairs in `eval/labelled_set.json` are `to_review`: every unsure label plus a random 10% of the rest. None has a `reviewed_by` yet. Each answer goes in `reviewed_by` and `review_note`, and overrides the label. Precision and recall are provisional until then.
- **Two disagreements on sure labels (13):**
  - **Bank of India, KYC compliance monitoring unit:** triage dropped it as "general IT project agency hiring", because triage sees only the bid document, not the attachments. To decide: is the label right, and if so, should triage of vague-title bids read the bid card or the attachments?
  - **National Housing Bank, training-content agency:** the Judge called it management consultancy; the label says it is not. To decide: which is right? If the label is, the management-consultancy topic definition needs to exclude training content.

## Appendix A. Candidate optimisations (not yet adopted)

These are further ways to cut model use and latency, on top of sections 8 and 9. None of them changes the design. Each is adopted only after its test passes, and the labelled set (section 13) is the yardstick. The two marked ★ are expected to save the most.

### A.1 Model use

| # | Idea | What it saves | How to test | Risk |
|---|---|---|---|---|
| M1 ★ | **Bid card.** When a bid is first downloaded, the model writes a ~300-token summary: buyer, scope, key requirements, eligibility, value. It's stored with the documents (6.3). Every later triage, for any topic or user, reads the card instead of page text. | The triage cost is paid once per bid, not once per question: ~1.5k → ~0.3k tokens per triage, shared across users and topics | Triage on cards vs on pages over the labelled set: no relevant bid lost, same Judge results | A card can miss a detail that matters for an unusual topic. The full Judge still reads the documents, so only triage depends on the card. |
| M2 ★ | **Reranker instead of model triage.** bge-reranker-v2-m3 (already in the local cache) scores each bid's card or first page against the topic `meaning`. Bids below a threshold are dropped without a model call. | Triage needs no LLM calls at all; runs on the local GPU or CPU | Tune the threshold on the labelled set so recall is 100% (no relevant bid below it), then measure how many bids it drops | Thresholds may differ by topic. Start with a per-topic threshold set conservatively low, plus model triage for the scores near the threshold. Note: reranking the *passage pool* lost top-8 recall on 20 Nov (3.5.1) — this idea is a different use (triage), but measure before wiring it. |
| M3 | **Batch triage.** About 10 bids per prompt, each with its own id, answered as a JSON list. | The instructions and topic definition are sent once per 10 bids instead of every time: fewer calls, fewer tokens | Same bids triaged singly and in batches: answers must match, and shuffling the batch order must not change any answer | One bid's text may influence another's answer. Keep batches small and shuffle them. |
| M4 (adopted 16 Nov: vLLM enforces `response_format` JSON schemas; all agents use NativeOutput) | **Typed JSON output.** Use the server's structured-output option (`response_format` with a JSON schema) for every agent answer. | Shorter answers, and no retries for malformed replies | Count retries and output tokens before and after over a day of runs | The server is vLLM (3.5); check that it honours the schema for this model. |
| M5 (checked 16 Nov: no hidden reasoning, completion tokens = visible answer) | **No hidden reasoning.** Measure whether Qwen spends tokens on reasoning that never shows in the answer (tokens billed vs visible answer length). If so, turn it off for triage and judging. | Possibly most of the output tokens per call | Compare `completion_tokens` with the visible answer on 20 calls; if reasoning is on, rerun the labelled set without it | Judging quality may drop without reasoning. Keep it on for the Judge if the labelled set says so. |
| M6 | **One verdict per auction family.** A reverse auction is judged by reusing its original bid's verdict, via `b_id_parent` (3.3). | One Judge call per reverse auction | Check on sample RAs that the RA's documents add nothing the parent didn't have | An RA can narrow the scope. Re-judge if the RA document's text differs much from the parent's. |
| M7 | **Stop early.** When the user asks for a number ("top 5"), stop judging once that many are confirmed, working in rank order. | The rest of the shortlist isn't judged | Check that the ranking puts relevant bids near the top on the labelled set | A better bid ranked lower is missed. Mention in the reply that more remain unchecked, and offer to continue. |

### A.2 Latency

| # | Idea | What it saves | How to test | Risk |
|---|---|---|---|---|
| L1 | **Background judging for saved topics.** As the index picks up new bids, those ranking high for a saved topic or daily search are downloaded and judged straight away. | Morning queries for common topics come back almost instantly | Time a saved-topic query before and after; count model calls spent on bids no one ever looks at | Wasted calls on bids no one opens. Limit it to saved topics and a daily budget. |
| L2 | **Speculative downloads.** Downloading the top-ranked bids starts while the Planner is still finishing. | Overlaps the downloads with planning (~10–20 s) | Measure time to first result with and without | A few downloads may turn out unneeded. They're cheap, and cleaned up by retention (11.1). |
| L3 | **Parallel text extraction and OCR.** Text and OCR run in a pool of worker processes, one PDF per core (32 cores). | OCR of scanned RFPs no longer holds up the queue | Time a scanned 38-page RFP, serial vs pooled | Memory use with many OCR jobs. Cap the pool size. |
| L4 | **Cached results first.** The reply opens with bids already judged for this topic ("6 you've already seen"), while new ones are still being checked. | The user sees answers in seconds | Time to first visible result | Cached verdicts may be stale if a corrigendum changed a bid. Check the corrigendum flag (one cheap request) before showing them. |
| L5 | **Faster embeddings.** A quantised ONNX build of all-MiniLM-L6-v2 for the page ranker (3.5.1), or a BM25 prefilter that embeds only the top ~200 chunks. | Bounds the dense cost on 1 300+ chunk bids (p95 is 13.5 s) | Embedding speed, and retrieval recall on the labelled set, must stay within 1% | Small accuracy loss from quantisation or truncation. |
