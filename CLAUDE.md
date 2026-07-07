# Working notes for Claude Code in this repo

This file is project-specific context for whoever (human or Claude) picks up work here next.
For the public-facing overview (links, repo structure, build/test commands), see `README.md`.

## Competition context

- Two linked competitions, same team, must enter both jointly:
  - **Simulation** (this repo's target): https://www.kaggle.com/competitions/pokemon-tcg-ai-battle
    Code submission, ranked on an automated ladder. No prize money. Deadline 2026-08-16.
  - **Strategy** (companion): https://www.kaggle.com/competitions/pokemon-tcg-ai-battle-challenge-strategy
    One written Writeup, human-judged. $240,000 prize pool. Deadline 2026-09-13.
- Battles run on the **cabt Engine** via the `cg` Python package (ctypes bindings). API docs:
  https://matsuoinstitute.github.io/cabt/
- Scoring: Gaussian skill rating N(μ, σ²), every new submission starts at **μ₀ = 600**. Rating
  only moves once real episodes are played and scored against other submissions on the ladder —
  a freshly-validated submission sitting at exactly 600 is not a signal of skill yet, just the
  starting point. New submissions get run more frequently at first specifically to establish
  their rating faster.

## Repo structure

```
agent/main.py          # our agent -- this is what gets packaged and submitted
agent/deck.csv          # 60 card IDs, one per line
scripts/build_submission.sh   # packages agent/ + engine bindings into build/submission.tar.gz
scripts/play_local_match.py   # runs two agents against the real cg engine locally, no Kaggle needed
data/                   # gitignored -- re-download with `kaggle competitions download -c pokemon-tcg-ai-battle -p data --unzip`
```

## Agent design (`agent/main.py`)

Rules-based heuristic agent (no search/ML yet -- see "Possible next steps" below). Key structure:

- `agent(obs_dict)` dispatches on `obs.select.type`, wrapped in try/except that falls back to a
  legal random pick on any exception. This is required, not defensive paranoia: the game spec
  explicitly says new enum values can be appended to `SelectType`/`OptionType`/`SelectContext`
  during the competition, so an unhandled case must degrade gracefully, never crash.
- `SelectType.MAIN` options are each a **fully-specified concrete action** (an EVOLVE option
  already carries both source and target, an ATTACK option already carries `attackId`, etc.) --
  ranked by a fixed tier (evolve > play > attach > ability > attack > retreat > discard > end),
  with attack/play sub-scored by weakness-adjusted damage / Pokemon HP.
- Retreat is threat-aware (`_retreat_is_urgent`): flees a doomed active before
  attach/ability/attack if the opponent could likely KO it next turn and our own attack wouldn't
  KO them first. An **ex/Mega‑ex active gets a lower survival bar** (`CardData.ex`/`megaEx`) --
  losing one hands the opponent an extra prize, so it's worth fleeing a hit we'd technically
  survive.
- `SelectType.CARD` (the generic "pick a card" select) is dispatched by `SelectContext` into
  three families: prefer-highest-value (setup/bench/heal/keep-in-place/evolve-target/to-hand),
  prefer-ready-to-attack (setup active/switch/retreat-target/attach-recipient), and
  discard-like (actually giving something up: discard/to-deck/to-prize). Contexts not classified
  fall back to random -- currently `ATTACH_TO`, `DETACH_FROM`, `LOOK`, `EFFECT_TARGET`,
  `EVOLVES_FROM` are deliberately left this way (too ambiguous to reason about generically
  without parsing card effect text).
- `SelectType.ENERGY`/`ATTACHED_CARD`/`CARD_OR_ATTACHED_CARD` (giving up an attached card, e.g.
  paying retreat cost) prefers shedding energy types held in redundant copies.
- `SelectType.SPECIAL_CONDITION` picks the most crippling condition (paralyze > sleep > confuse >
  burn/poison), whether inflicting it on the opponent or curing our own.

**Key lesson learned twice now**: several `cg/api.py` docstring assumptions turned out wrong or
incomplete when checked against real self-play (e.g. retreat-cost discard actually surfaces as
`SelectType.ENERGY` not `ATTACHED_CARD`; `TO_HAND`/`ATTACH_FROM` turned out to be common contexts,
not edge cases, once instrumented and counted). **Always validate against the live engine**, not
just the field docs.

## Validation workflow

Before committing any change to `agent/main.py`:

```bash
python scripts/play_local_match.py 30     # sanity-check win rate vs. the sample random-move agent
```

For anything touching a specific `SelectType`/`SelectContext` combination that might be rare,
temporarily wrap `_decide` in an instrumented copy of `play_local_match.py` that counts
`(select.type, select.context)` occurrences and catches exceptions from `_decide` directly (not
just the outer `agent()` try/except) -- this is how the retreat-urgency logic and the
special-condition/NOT_MOVE/TO_HAND fixes were actually confirmed to fire correctly, since natural
self-play doesn't always exercise every path in a small sample. Delete the instrumentation script
when done; it's scratch, not a permanent test suite.

## Submission workflow

```bash
./scripts/build_submission.sh     # -> build/submission.tar.gz (main.py at top level, deck.csv, cg/)
kaggle competitions submit -c pokemon-tcg-ai-battle -f build/submission.tar.gz -m "message"
kaggle competitions submissions -c pokemon-tcg-ai-battle   # check status: PENDING -> COMPLETE/Error
```

Every submission first runs a scheduled self-play validation episode before joining the
matchmaking pool. Max 5 submissions/day; only the latest 2 are actively scored.

## Status log

- **2026-07-07**: First submission (`ref 54436033`, "Inital Submission") went through the
  greedy-heuristic agent with the select-coverage/ex-retreat fixes (commit `8e69046`). Validated
  and `COMPLETE` at μ₀=600, rank ~2000/4505 (top 50 sit at 1100+) -- expected for a brand-new
  submission that hasn't accumulated real episode results yet, not a performance signal.

## Possible next steps (not started, needs a decision first)

- Wait for the submission's rating to move as episodes actually play out, then look at *how*
  it's winning/losing (episode replays, via the Submissions page or `kaggle` CLI) before deciding
  where to invest further -- don't guess blind.
- `cg/api.py` exposes a full game-tree search API (`search_begin`/`search_step`/`search_end`/
  `search_release`) that lets an agent simulate forward with the real engine given predicted
  opponent hands/decks -- i.e. minimax/MCTS instead of hand-tuned heuristics. This is the biggest
  lever available but a substantial project on its own (opponent modeling, search depth/budget
  control, proper search-state cleanup) -- not a quick addition.
- Deck composition (`agent/deck.csv`) hasn't been revisited since it was copied from the sample
  submission -- deckbuilding is a separate, large topic from agent decision logic.
