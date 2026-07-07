# Pokémon TCG AI Battle

Agent for The Pokémon Company's Kaggle challenge — build a Pokémon TCG agent that plays
against other submitted agents on an automated ladder, then (separately) write up the
strategy behind it for a shot at the $240,000 prize track.

- **Simulation** (this repo's main target): https://www.kaggle.com/competitions/pokemon-tcg-ai-battle
  Submit code, ranked by a TrueSkill-style skill rating. No prize money, deadline 2026-08-16.
- **Strategy** (companion, mandatory joint entry, same team): https://www.kaggle.com/competitions/pokemon-tcg-ai-battle-challenge-strategy
  Submit one written Writeup, judged by humans. $240,000 total prize pool, deadline 2026-09-13.

## Repo structure

```
.
├── agent/              # our agent — this is what gets packaged and submitted
│   ├── main.py          #   agent(obs_dict) -> list[int]
│   └── deck.csv          #   60 card IDs, one per line
├── scripts/
│   └── build_submission.sh   # packages agent/ + engine bindings into build/submission.tar.gz
├── data/                # gitignored — raw Kaggle-provided competition materials (see below)
└── notes/                # gitignored-optional scratch notes for the eventual Strategy writeup
```

`data/` is **not** tracked in git — the competition rules prohibit redistributing the
provided card data, engine source, and compiled engine binaries. Re-download it yourself:

```bash
kaggle competitions download -c pokemon-tcg-ai-battle -p data --unzip
```

That gives you (paths as flattened locally):
- `data/EN_Card_Data.csv`, `data/JP_Card_Data.csv` — card metadata
- `data/Card_ID List_EN.pdf`, `data/Card_ID List_JP.pdf` — visual card reference
- `data/ptcg_engine/` — C++20 header-only "cabt Engine" source (Visual Studio 2022 project)
- `data/sample_submission/` — Kaggle's example agent, including the compiled `cg` engine
  bindings (`cg.dll` / `libcg.so` / `libcg-arm64.so` / `libcg.dylib`) needed to run battles
  locally

## Agent contract

`agent/main.py` must define:

```python
def agent(obs_dict: dict) -> list[int]:
    ...
```

- First call: `obs.select is None` → return the 60-card deck (list of card IDs) read from `deck.csv`.
- Every other call: return a list of option indices, length between `obs.select.minCount` and
  `obs.select.maxCount`, no duplicates, each index `< len(obs.select.option)`.
- At runtime, files live at `/kaggle_simulations/agent/`.

## Local testing

The `cg` package (in `data/sample_submission/sample_submission/cg/`) wraps the same engine
binaries used server-side via `ctypes`, so battles can be run locally without submitting:

```python
import sys
sys.path.insert(0, "data/sample_submission/sample_submission")
from cg.game import battle_start, battle_select, battle_finish
```

Use this to play two decks/agents against each other and iterate before submitting.

## Building a submission

```bash
./scripts/build_submission.sh
```

Produces `build/submission.tar.gz` with `main.py` at the top level (not nested) plus
`deck.csv` and the `cg/` engine bindings — ready to upload as a competition submission.

## Scoring

Gaussian skill rating N(μ, σ²), μ₀ = 600, TrueSkill/Elo-like (opponent rating + win/loss,
not margin of victory). Only your latest 2 submissions are actively scored; max 5
submissions/day; max team size 5.

## License

Original code in this repo is MIT-licensed (see `LICENSE`). Kaggle-provided competition
materials in `data/` are **not** covered by that license and must not be redistributed —
see the license note at the bottom of `LICENSE` and the competition rules.
