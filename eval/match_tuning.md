# Match prompt tuning

## Method

The baseline is prompt version 1 from Task 10's preserved live run at commit
`d0b5cf0`; it was not rerun. Each candidate was scored once with three repeats:

```bash
DATA_DIR=.superpowers/live-eval-data uv run gem-agent eval match
```

The table reports the worst of the three repeats. Versions 2–4 used the original
subject-topic gate: keep a candidate only when both A's checked misses and A's all
misses are no higher than the previous kept version. The correctness correction
added version 5, which also had to restore A's buyer-topic worst-run checked and
all misses to exactly 0. Failure of either v5 condition reverts all tuning to
prompt version 1. Buyer-topic results remain tabulated separately. A negative
read means a labelled irrelevant tender was still read.

## Tuning table

| Version | Prompt change | A checked misses (runs; worst) | A all misses (runs; worst) | A negatives read (runs; worst) | Today-or-A checked misses (runs; worst) | Decision |
|---|---|---|---|---|---|---|
| 1 | Task 10 baseline | 2/2/3; **3** | 8/9/10; **10** | 64/66/67; **67/354** | 1/1/1; **1** | Baseline (kept) |
| 2 | Payments: card-management or issuing software is `match` when the topic's own systems are bought | 2/2/1; **2** | 9/9/7; **9** | 73/75/74; **75/354** | 1/1/1; **1** | **Keep** (2 ≤ 3 and 9 ≤ 10) |
| 3 | Named module in a broader suite: `match` when named, `unsure` when only the suite is named (on v2) | 2/2/2; **2** | 10/10/9; **10** | 55/58/52; **58/354** | 2/2/2; **2** | **Revert** (all misses 10 > 9) |
| 4 | Same-family product: `unsure`, never `no` (on kept v2) | 1/1/2; **2** | 7/8/9; **9** | 76/67/75; **76/354** | 1/1/1; **1** | **Keep** (2 ≤ 2 and 9 ≤ 9) |
| 5 | Buyer-topic matching takes precedence over what-is-bought tuning (on v4) | 2/3/1; **3** | 7/11/6; **11** | 79/50/80; **80/354** | 1/1/1; **1** | **Revert all tuning to v1** (subject 3 > 2, 11 > 9; buyer 1 ≠ 0) |

The v2–v4 decisions record what was kept at that time. The v5 correctness ruling
supersedes them: because v5 failed, the final prompt is version 1 and no tuning
is kept.

## Buyer topics (scored apart)

| Version | A buyer checked misses (runs; worst) | A buyer all misses (runs; worst) | Today-or-A buyer checked misses (runs; worst) |
|---|---|---|---|
| 1 | 0/0/0; **0** | 0/0/0; **0** | 0/0/0; **0** |
| 2 | 16/14/1; **16** | 17/15/1; **17** | 7/6/1; **7** |
| 3 | 0/1/16; **16** | 0/1/17; **17** | 0/1/7; **7** |
| 4 | 14/14/14; **14** | 15/15/15; **15** | 6/6/6; **6** |
| 5 | 1/0/1; **1** | 1/0/1; **1** | 1/0/1; **1** |

## Outcome

Final `MATCH_PROMPT_VERSION` is **"1"**. **No tuning was kept.** Version 5 failed
both required conditions: A's subject worst-run checked/all misses were **3/11**
(limit **2/9**), and A's buyer worst-run checked/all misses were **1/1** (required
**0/0**). Per the correctness ruling, `gem_agent/agents/matcher.py` was reverted to
the original version-1 prompt, withdrawing the earlier v2/v4 subject gains rather
than shipping a buyer-topic regression.

Full live outputs and pre-check evidence are preserved under
`.superpowers/sdd/2026-11-24-describe-once/task-11-v{2,3,4,5}-*` (not committed).

## Later contract change: prompt version 2

After this tuning round, the matcher output contract changed from two lists
(`match`, `unsure`; everything else implied `no`) to three lists (`match`,
`unsure`, `no`) with every bracketed bid required to appear exactly once.
`MATCH_PROMPT_VERSION` is now **"2"**. This is a correctness change, not a
tuning candidate. The table above describes version 1 and its tuning candidates
only.

Version 2 was scored on 26 Nov 2026 with three repeats on the same labelled set.
One 29-bid batch returned 28 indices, so the exact-once check split it; the
other batches completed normally. Subject-topic worst-run results:

| Version | A checked misses (runs; worst) | A all misses (runs; worst) | A negatives read (runs; worst) | Today-or-A checked misses (runs; worst) | A buyer checked misses (runs; worst) |
|---|---|---|---|---|---|
| 2 | 2/3/3; **3** | 7/9/10; **10** | 66/43/69; **69/354** | 1/1/1; **1** | 1/1/1; **1** |

Version 2 therefore ties version 1 on checked and all misses, reads slightly
more negatives (69 vs 67), and records one buyer-topic checked miss instead of
zero. The safety net remains necessary and stays enabled.

## Buyer-topic miss was a harness gap (26 Nov 2026)

The version 2 buyer-topic miss was the same tender in every run: 10101986, coin
minting machine parts for India Government Mint (Security Printing and Minting
Corporation of India, which GeM lists under the Ministry of Finance).

**Cause:** 73 labelled bids, 7 of them `mof`, are no longer in the bid cache.
The harness rebuilt them from their labels with no ministry or department, so
the matcher's buyer line held only the organisation name. A live search always
has the ministry from GeM's listing. Six of the seven still matched because
their organisation names say Income Tax or CBEC; the Mint's does not. Those
same seven were exactly today's seven buyer-topic misses in every run, so
today's buyer score was understated for the same reason.

**Check, before the fix:**

- Matched alone, 10101986 was `match` in 3 of 3 runs, with or without the ministry.
- In the full 29-bid batch, it was missed in 3 of 3 runs as rebuilt, and in 0 of 3
  once "Ministry Of Finance" was filled in for the seven rebuilt bids.

**Fix:** a bid rebuilt from its label now takes its ministry and department from
the cached bid document's first page (`tools/gem/bid_fields.py`). The report
lists any buyer-topic bid still without a ministry. On the current set, all 73
rebuilt labels get one.

**Version 2, buyer topics, after the fix** (`gem-agent eval match --topic mof
--repeats 3`):

| Way | Checked misses (runs) | All misses (runs) | Negatives read (runs) |
|---|---|---|---|
| today | 0/0/0 | 0/0/0 | 0/5 each |
| A | 0/0/0 | 0/0/0 | 0/5 each |
| today or A | 0/0/0 | 0/0/0 | 0/5 each |

Version 2 therefore has no buyer-topic regression. The buyer results in the
tuning table above (v1–v5) were scored with the same gap, so they understate
both ways. The large v2/v4 buyer swings may still be real, but they were not
measured fairly.

The fix also changes the buyer line for the 66 rebuilt subject-topic bids, so the
subject-topic numbers above should be re-scored before they are compared with
later versions.
