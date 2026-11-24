# Match prompt tuning

## Method

The baseline is prompt version 1 from Task 10's preserved live run at commit
`d0b5cf0`; it was not rerun. Each candidate was scored once with three repeats:

```bash
DATA_DIR=.superpowers/live-eval-data uv run gem-agent eval match
```

The table reports the worst of the three repeats. Subject-topic misses alone gate
the decision: keep a candidate only when both A's checked misses and A's all
misses are no higher than the previous kept version. Buyer-topic results are
reported separately and never enter that gate. A negative read means a labelled
irrelevant tender was still read.

## Tuning table

| Version | Prompt change | A checked misses (runs; worst) | A all misses (runs; worst) | A negatives read (runs; worst) | Today-or-A checked misses (runs; worst) | Decision |
|---|---|---|---|---|---|---|
| 1 | Task 10 baseline | 2/2/3; **3** | 8/9/10; **10** | 64/66/67; **67/354** | 1/1/1; **1** | Baseline (kept) |
| 2 | Payments: card-management or issuing software is `match` when the topic's own systems are bought | 2/2/1; **2** | 9/9/7; **9** | 73/75/74; **75/354** | 1/1/1; **1** | **Keep** (2 ≤ 3 and 9 ≤ 10) |
| 3 | Named module in a broader suite: `match` when named, `unsure` when only the suite is named (on v2) | 2/2/2; **2** | 10/10/9; **10** | 55/58/52; **58/354** | 2/2/2; **2** | **Revert** (all misses 10 > 9) |
| 4 | Same-family product: `unsure`, never `no` (on kept v2) | 1/1/2; **2** | 7/8/9; **9** | 76/67/75; **76/354** | 1/1/1; **1** | **Keep** (2 ≤ 2 and 9 ≤ 9) |

## Buyer topics (scored apart)

| Version | A buyer checked misses (runs; worst) | A buyer all misses (runs; worst) | Today-or-A buyer checked misses (runs; worst) |
|---|---|---|---|
| 1 | 0/0/0; **0** | 0/0/0; **0** | 0/0/0; **0** |
| 2 | 16/14/1; **16** | 17/15/1; **17** | 7/6/1; **7** |
| 3 | 0/1/16; **16** | 0/1/17; **17** | 0/1/7; **7** |
| 4 | 14/14/14; **14** | 15/15/15; **15** | 6/6/6; **6** |

## Outcome

Final prompt version is **4**: version 2's payments rule plus version 4's
same-family rule. Version 3 was tried and reverted. Against the preserved
baseline, A's worst-run subject-topic checked misses improved from **3 to 2**
and all misses from **10 to 9**; today-or-A checked misses remain **1**.
A's buyer-topic misses remain worse than baseline and are a known concern,
kept separate from the subject-topic gate.

Full live outputs and pre-check evidence are preserved under
`.superpowers/sdd/2026-11-24-describe-once/task-11-v{2,3,4}-*` (not committed).
