"""Run local battles between two agents directly against the cg engine (no Kaggle needed).

Requires the competition data to be downloaded into ./data first (see README).

Usage:
    python scripts/play_local_match.py [num_games]
"""
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CG_PARENT = os.path.join(REPO_ROOT, "data", "sample_submission", "sample_submission")
sys.path.insert(0, CG_PARENT)

from cg.game import battle_finish, battle_select, battle_start  # noqa: E402
from cg.api import to_observation_class  # noqa: E402


def load_agent(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.agent


def load_deck(path: str) -> list[int]:
    with open(path) as f:
        lines = f.read().split("\n")
    return [int(x) for x in lines[:60]]


def play_one(agents, decks) -> int:
    obs_dict, start_data = battle_start(decks[0], decks[1])
    if not start_data.battlePtr:
        raise RuntimeError(
            f"BattleStart failed: errorPlayer={start_data.errorPlayer} errorType={start_data.errorType}"
        )

    for _ in range(20000):
        obs = to_observation_class(obs_dict)
        if obs.current is None or obs.current.result != -1:
            result = obs.current.result if obs.current is not None else 2
            break
        select_list = agents[obs.current.yourIndex](obs_dict)
        obs_dict = battle_select(select_list)
    else:
        result = 2  # safety valve: treat runaway games as a draw

    battle_finish()
    return result


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    heuristic_agent = load_agent(os.path.join(REPO_ROOT, "agent", "main.py"), "heuristic_agent")
    baseline_agent = load_agent(os.path.join(CG_PARENT, "main.py"), "baseline_agent")
    heuristic_deck = load_deck(os.path.join(REPO_ROOT, "agent", "deck.csv"))
    baseline_deck = load_deck(os.path.join(CG_PARENT, "deck.csv"))

    tally = {"heuristic": 0, "baseline": 0, "draw": 0, "error": 0}
    for i in range(n):
        # alternate seating so first-player advantage cancels out
        if i % 2 == 0:
            agents = [heuristic_agent, baseline_agent]
            decks = [heuristic_deck, baseline_deck]
            heuristic_slot = 0
        else:
            agents = [baseline_agent, heuristic_agent]
            decks = [baseline_deck, heuristic_deck]
            heuristic_slot = 1

        try:
            result = play_one(agents, decks)
        except Exception as e:
            print(f"game {i}: ERROR {e!r}")
            tally["error"] += 1
            continue

        if result == 2:
            outcome = "draw"
        elif result == heuristic_slot:
            outcome = "heuristic"
        else:
            outcome = "baseline"
        tally[outcome] += 1
        print(f"game {i}: {outcome} wins (result={result}, heuristic was slot {heuristic_slot})")

    print(f"\n{n} games -> heuristic {tally['heuristic']}, baseline {tally['baseline']}, "
          f"draw {tally['draw']}, error {tally['error']}")


if __name__ == "__main__":
    main()
