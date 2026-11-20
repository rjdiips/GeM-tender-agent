# PROTOTYPE: chat UI layouts (archived, not built)

Question: what should the GeM tender chat page look like? (spec 4.1)

Round 1 (16 Nov): three layouts. The user chose A's layout (history left, chat centre, results panel right) and
asked for a sleeker, more professional design. The two other layouts are in `_discarded/`.

Round 2: four variants of A's layout, switched with `?variant=A|B|C|D` (arrow keys or the bottom-right bar):
A original · B "Refined light" · C "Midnight pro" · D "Enterprise fintech" · E = C in light mode
(C also has a sun/moon toggle in its header). The user liked B and C.
Read-only, in-memory sample data; nothing talks to the backend.

Run: `npm --prefix web run prototype`, then open the printed URL with `?variant=A`.

When a variant wins: rewrite it properly in web/src/, then delete this folder.
(The skill says to keep the variants on a throwaway git branch; the project has no git repo yet.)

## Verdict (16 Nov)

Chosen: **variant C "Midnight pro", with both dark and light modes** (light = variant E). Layout from round 1's
variant A: history sidebar | chat thread | results panel (Relevant / Quarantine / Memory). The user also liked B.
Rebuilt properly in `web/src/`; these files are the record of the prototype and are not part of the build.
To view them again, copy this folder back to `web/src/prototype/chat` and mount `ChatPrototype` in App.tsx.
