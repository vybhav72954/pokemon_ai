"""Benchmark two agents head-to-head against the real cg engine, with instrumentation
that reveals *how* games end -- not just the win rate.

Unlike play_local_match.py (which is hardwired to heuristic-vs-sample-baseline), this
takes two arbitrary agent files, so you can A/B a candidate change against the current
agent, or mirror the agent against itself to study how its own games unfold.

Usage:
    python scripts/benchmark.py [n] [agentA.py] [agentB.py] [deckA.csv] [deckB.csv]

Defaults: n=50, agentA = agentB = our agent (a mirror match), both using agent/deck.csv.
A mirror match is ~50% by symmetry -- the point isn't the win rate, it's the game-shape
stats (length, prize differential, deck-out rate) that tell you whether the heuristic
actually closes games out or just flails until someone decks out.
"""
import importlib.util
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CG_PARENT = os.path.join(REPO_ROOT, "data", "sample_submission", "sample_submission")
sys.path.insert(0, CG_PARENT)

from cg.game import battle_finish, battle_select, battle_start  # noqa: E402
from cg.api import to_observation_class  # noqa: E402

STARTING_PRIZES = 6  # standard Pokemon TCG prize count; used to infer prizes taken


def load_agent(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.agent


def load_deck(path: str) -> list[int]:
    with open(path) as f:
        lines = f.read().split("\n")
    return [int(x) for x in lines[:60]]


def play_one(agents, decks) -> dict:
    """Play one game; return a dict describing how it ended."""
    obs_dict, start_data = battle_start(decks[0], decks[1])
    if not start_data.battlePtr:
        raise RuntimeError(
            f"BattleStart failed: errorPlayer={start_data.errorPlayer} errorType={start_data.errorType}"
        )

    obs = None
    peak_board = [0, 0]  # max (active+bench) each slot ever reached -- setup quality
    for _ in range(20000):
        obs = to_observation_class(obs_dict)
        if obs.current is None or obs.current.result != -1:
            break
        for i, p in enumerate(obs.current.players):
            peak_board[i] = max(peak_board[i], len(p.active) + len(p.bench))
        select_list = agents[obs.current.yourIndex](obs_dict)
        obs_dict = battle_select(select_list)
    else:
        obs = to_observation_class(obs_dict)  # runaway; capture whatever we have

    info = {"result": 2, "turn": 0, "prizes_left": [STARTING_PRIZES, STARTING_PRIZES],
            "deck_left": [0, 0], "peak_board": peak_board, "runaway": False}
    if obs is not None and obs.current is not None:
        st = obs.current
        info["result"] = st.result if st.result != -1 else 2
        info["turn"] = st.turn
        info["prizes_left"] = [len(p.prize) for p in st.players]
        info["deck_left"] = [p.deckCount for p in st.players]
    battle_finish()
    return info


def classify(info: dict, winner_slot: int) -> str:
    """Best-effort reason the game ended, from the final board state."""
    if winner_slot is None:
        return "draw"
    loser_slot = 1 - winner_slot
    if info["deck_left"][loser_slot] == 0:
        return "deckout"          # loser ran out of cards to draw
    if info["prizes_left"][winner_slot] == 0:
        return "prizes"           # winner took all their prizes (KO race)
    return "no-pokemon"           # loser had no Pokemon left to fight (or other)


def pct_ci(wins: int, decided: int) -> tuple[float, float]:
    """Win rate and 95% margin of error (normal approx), over decided games only."""
    if decided == 0:
        return (0.0, 0.0)
    p = wins / decided
    moe = 1.96 * math.sqrt(p * (1 - p) / decided)
    return (p, moe)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    a_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO_ROOT, "agent", "main.py")
    b_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(REPO_ROOT, "agent", "main.py")
    a_deck_path = sys.argv[4] if len(sys.argv) > 4 else os.path.join(REPO_ROOT, "agent", "deck.csv")
    b_deck_path = sys.argv[5] if len(sys.argv) > 5 else a_deck_path

    agent_a = load_agent(a_path, "agent_a")
    agent_b = load_agent(b_path, "agent_b")
    deck_a = load_deck(a_deck_path)
    deck_b = load_deck(b_deck_path)

    label_a = os.path.relpath(a_path, REPO_ROOT)
    label_b = os.path.relpath(b_path, REPO_ROOT)
    mirror = os.path.abspath(a_path) == os.path.abspath(b_path)
    print(f"A = {label_a}\nB = {label_b}" + ("   (mirror match)" if mirror else ""))
    print(f"running {n} games (seats alternate each game)...\n")

    tally = {"A": 0, "B": 0, "draw": 0, "error": 0}
    reasons = {"prizes": 0, "deckout": 0, "no-pokemon": 0, "draw": 0}
    turns, prize_margins = [], []
    peak_a, peak_b = [], []  # per-side peak board size, to measure brick rate

    for i in range(n):
        # A occupies slot 0 on even games, slot 1 on odd -- cancels first-player edge.
        a_slot = 0 if i % 2 == 0 else 1
        agents = [agent_a, agent_b] if a_slot == 0 else [agent_b, agent_a]
        decks = [deck_a, deck_b] if a_slot == 0 else [deck_b, deck_a]

        try:
            info = play_one(agents, decks)
        except Exception as e:
            print(f"game {i}: ERROR {e!r}")
            tally["error"] += 1
            continue

        result = info["result"]
        if result == 2:
            winner_slot = None
            tally["draw"] += 1
            outcome = "draw"
        else:
            winner_slot = result
            outcome = "A" if result == a_slot else "B"
            tally[outcome] += 1

        reason = classify(info, winner_slot)
        reasons[reason] += 1
        turns.append(info["turn"])
        peak_a.append(info["peak_board"][a_slot])
        peak_b.append(info["peak_board"][1 - a_slot])
        if winner_slot is not None:
            prize_margins.append(STARTING_PRIZES - info["prizes_left"][1 - winner_slot])

        pl = info["prizes_left"]
        print(f"game {i}: {outcome:5} ({reason:10}) turn={info['turn']:3}  "
              f"prizes_left A/B={pl[a_slot]}/{pl[1 - a_slot]}")

    decided = tally["A"] + tally["B"]
    p, moe = pct_ci(tally["A"], decided)
    print(f"\n{'='*60}")
    print(f"{n} games -> A {tally['A']}, B {tally['B']}, draw {tally['draw']}, error {tally['error']}")
    if decided:
        print(f"A win rate (decided games): {p*100:.1f}% +/- {moe*100:.1f}%")
    if turns:
        print(f"game length: avg {sum(turns)/len(turns):.1f} turns, "
              f"min {min(turns)}, max {max(turns)}")
    if prize_margins:
        avg_margin = sum(prize_margins) / len(prize_margins)
        print(f"winner's prizes taken: avg {avg_margin:.2f} / {STARTING_PRIZES} "
              f"(6 = clean KO-race win)")
    print(f"end reasons: {reasons}")

    def setup_line(label, peaks):
        if not peaks:
            return f"{label}: (no data)"
        brick = 100 * sum(p <= 1 for p in peaks) / len(peaks)
        weak = 100 * sum(p <= 2 for p in peaks) / len(peaks)
        return (f"{label}: avg peak board {sum(peaks)/len(peaks):.2f}   "
                f"bricked (<=1) {brick:.0f}%   weak setup (<=2) {weak:.0f}%")
    print("\n--- setup quality (lower brick % is better) ---")
    print(setup_line("A", peak_a))
    print(setup_line("B", peak_b))


if __name__ == "__main__":
    main()
