# Live GeM search tests

These tests ask one question: if the local index (`bids.sqlite`, synced by systemd timers) is replaced by searching GeM live
each time a user asks, which searches find the relevant bids, and how long do they take?

The scripts only read from GeM (`all-bids-data` and `search-bids`, open bids only), through the shared rate limiter
(one request at a time, at least 1 s apart). The relevant bids come from [`eval/labelled_set.json`](../labelled_set.json).

## Run

From the repo root (`HF_HUB_OFFLINE=1`; the scripts need no models):

```
uv run --no-sync python eval/live_search/scripts/live_counts.py   <out>/term_counts.json
uv run --no-sync python eval/live_search/scripts/live_recall.py   <out>/term_counts.json <out>/term_recall.json
uv run --no-sync python eval/live_search/scripts/live_category.py <out>/category_search.json
```

| Script | Requests | What it does |
|---|---|---|
| `live_counts.py` | ~130 | Page 1 of every topic's `match_terms`, in both `fullText` and `exact` mode: how many bids GeM says match |
| `live_recall.py` | ~1,600 (about 30 min) | Reads every term search with 1 to 3,000 matches to the end. Records which relevant labelled bids it found, on which page, and the seconds per page. A search whose page 1 equals an earlier search's page 1 is not read again (`same_as`) |
| `live_category.py` | ~520 | The two vague-title categories ("Hiring of Agency for IT Projects- Milestone Basis", "Custom Bid for Services"), read in full by category and by keyword. Checks whether they reach the relevant vague-title bids and the open vague-title bids in the old index (read-only) |

## Results

`results/<date>/` holds the raw output of one run:

| File | Contents |
|---|---|
| `term_counts.json` | `[{topic, term, mode, num_found, secs}]` |
| `term_recall.json` | `results["<mode>\|<term>"] = {num_found, pages, unique, found: {b_id: page}}` (or `same_as`), and `secs`: the time of each page request |
| `term_recall_output.txt` | Console output. `rel=` counts relevant bids of **all** topics, not only the term's own topic |
| `category_search.json` | Per search: `num_found`, `pages`, `secs`, `ids`, `vague`; plus `relevant_vague_reached` and `index_vague_reached` |
| `category_search_output.txt` | Console output |

### 2026-11-18

Notes on this run:
- `live_recall.py` and `live_category.py` ran at the same time and shared the rate limit, so the category times are about double what they would be on their own.
- Disk pressure was 2–30% during the run (`/proc/pressure/io`, avg10).

**Speed:** across 1,638 pages, the median was 1.0 s per page and the 90th percentile 1.26 s.

**GeM search behaviour:**
- `exact` mode returns 0 for every title term. It only matches ministry and department names ("Ministry of Finance" returns 932, "Department of Financial Services" 473).
- `fullText` counts are erratic:
  - "KYC" returns 0, but "Know Your Customer" returns 710.
  - "Anti-Money Laundering" returns 0, but "AML" returns 91.
  - "AI Platform", "AI Software" and "AI Infrastructure" return identical results (2,880–2,885).
  - Department names in `fullText` mode return ~33,000, nearly the whole list.

**Where the relevant bids came from (open bids only):**

| Topic | Relevant | Found by | Smallest search that found them |
|---|---|---|---|
| AML, fraud, KYC, LOS | 3, 3, 3, 2 | IT Projects category only; keyword terms found 0 | category, 171 bids (18 pages, 18 s) |
| AI | 12 | 4 by searches of 32 or 144 bids, 2 by the category, 6 only by the 2,880-bid "AI …" search | — |
| Ministry of Finance | 5 | `exact` department searches | 243 and 473 bids |
| Consulting | 1 | no search run; the title is the category "Hiring of Consultants - Milestone/Deliverable Based" | — |
| RBI | 0 | — | — |

A 300-bid cap on each search would have lost 6 of 12 AI bids and 1 of 5 Ministry of Finance bids.

**Vague-title categories:**

| Search | Bids returned | Vague-title bids | Pages | Time |
|---|---|---|---|---|
| Category "Hiring of Agency for IT Projects" | 171 | 162 | 18 | 18 s |
| Category "Custom Bid for Services" | 2,497 | 86 | 250 | 650 s (shared limit) |
| Keyword "Hiring of Agency for IT Projects" | 1,803 | 162 | 181 | 218 s |
| Keyword "Custom Bid for Services" | 2,546 | 86 | 255 | 270 s |

- **Relevant vague-title bids:** the categories reached 9 of 10. The one missed, 9882990, closed at 16:00 that day, before the search reached it.
- **All vague-title bids:** they reached 243 of the 247 open vague-title bids in the old index.

**Limit:** the labelled set's relevant bids were picked from what the old index surfaced, so this run cannot show relevant bids that only a live search finds.
