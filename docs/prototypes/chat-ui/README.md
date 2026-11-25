# PROTOTYPE: chat UI layouts (archived, not built)

Question: what should the GeM tender chat page look like? (spec 4.1)

Round 1 (16 Nov): three layouts. The user chose A's layout (history left, chat centre, results panel right) and
asked for a sleeker, more professional design. The other two round 1 layouts (results-first, report) were deleted
on 23 Nov; they live in git history only.

Round 2: four variants of A's layout, switched with `?variant=A|B|C|D` (arrow keys or the bottom-right bar):
A original · B "Refined light" · C "Midnight pro" · D "Enterprise fintech" · E = C in light mode
(C also has a sun/moon toggle in its header). The user liked B and C.
Read-only, in-memory sample data; nothing talks to the backend.

Every variant now lives in `_discarded/` — the shipped page is in `web/src/`, so none of these are in the build.
`data.ts`, `useReplay.ts`, `icons.tsx`, `ChatPrototype.tsx` and `PrototypeSwitcher.tsx` stay here as the shared
harness they import from.

Run: point Vite at this folder (`index.html` → `src` importing `ChatPrototype`), then open `?variant=A`.

## Verdict (16 Nov)

Chosen: **variant C "Midnight pro", with both dark and light modes** (light = variant E). Layout from round 1's
variant A: history sidebar | chat thread | results panel (Relevant / Quarantine / Memory). The user also liked B.
Rebuilt properly in `web/src/`; these files are the record of the prototype and are not part of the build.
