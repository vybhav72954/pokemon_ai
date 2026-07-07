import os
import random

from cg.api import (
    Attack,
    AreaType,
    Card,
    CardData,
    CardType,
    Observation,
    Option,
    OptionType,
    SelectContext,
    SelectData,
    SelectType,
    all_attack,
    all_card_data,
    to_observation_class,
)

# Static card/attack databases, keyed by ID. Loaded once at import time; used to turn
# option indices into actual game knowledge (damage, HP, weakness...) for scoring.
_CARDS: dict[int, CardData] = {c.cardId: c for c in all_card_data()}
_ATTACKS: dict[int, Attack] = {a.attackId: a for a in all_attack()}


def read_deck_csv() -> list[int]:
    file_path = "deck.csv"
    if not os.path.exists(file_path):
        file_path = "/kaggle_simulations/agent/" + file_path
    with open(file_path, "r") as file:
        csv = file.read().split("\n")
    deck = []
    for i in range(60):
        deck.append(int(csv[i]))
    return deck


def _card(card_id: int | None) -> CardData | None:
    if card_id is None:
        return None
    return _CARDS.get(card_id)


def _attack(attack_id: int | None) -> Attack | None:
    if attack_id is None:
        return None
    return _ATTACKS.get(attack_id)


def _hand_card(obs: Observation, index: int | None) -> CardData | None:
    if index is None:
        return None
    hand = obs.current.players[obs.current.yourIndex].hand
    if not hand or index >= len(hand):
        return None
    return _card(hand[index].id)


def _pokemon_at(obs: Observation, player_index: int, area: AreaType | None, index: int | None):
    if area is None or index is None:
        return None
    player = obs.current.players[player_index]
    if area == AreaType.ACTIVE:
        pool = player.active
    elif area == AreaType.BENCH:
        pool = player.bench
    else:
        return None
    if index >= len(pool):
        return None
    return pool[index]


def _attack_score(attacker_type, defender_card: CardData | None, atk: Attack | None) -> float:
    """Rough expected value of an attack: raw damage, doubled on a weakness hit,
    docked a bit on a resistance hit. Good enough to rank options, not a rules engine."""
    if atk is None:
        return 0.0
    score = float(atk.damage)
    if defender_card is not None and attacker_type is not None:
        if defender_card.weakness == attacker_type:
            score *= 2
        if defender_card.resistance == attacker_type:
            score -= 20
    return score


def _affordable_attacks(card: CardData | None, energy_count: int) -> list[Attack]:
    """Attacks a Pokemon could pay for with its current energy count. Ignores exact
    color requirements (colorless vs. specific types) -- a rough but cheap filter."""
    if card is None:
        return []
    attacks = (_attack(aid) for aid in card.attacks)
    return [atk for atk in attacks if atk is not None and len(atk.energies) <= energy_count]


def _best_score_against(attacker_type, energy_count: int, attacker_card: CardData | None,
                         defender_card: CardData | None) -> float:
    return max(
        (_attack_score(attacker_type, defender_card, atk) for atk in _affordable_attacks(attacker_card, energy_count)),
        default=0.0,
    )


def _retreat_is_urgent(obs: Observation) -> bool:
    """True when our active is likely doomed next turn and attacking now wouldn't
    secure a KO anyway -- i.e. fleeing to a healthier bench Pokemon beats attacking."""
    state = obs.current
    me = state.players[state.yourIndex]
    opp = state.players[1 - state.yourIndex]
    active = me.active[0] if me.active else None
    defender = opp.active[0] if opp.active else None
    if active is None or not me.bench:
        return False

    active_card = _card(active.id)
    if active_card is None:
        return False
    defender_card = _card(defender.id) if defender else None

    our_best = _best_score_against(active_card.energyType, len(active.energies), active_card, defender_card)
    if defender is not None and our_best >= defender.hp:
        return False  # we can knock them out this turn -- just attack

    threat = 0.0
    if defender is not None and defender_card is not None:
        threat = _best_score_against(defender_card.energyType, len(defender.energies), defender_card, active_card)
    if threat < active.hp:
        return False  # we'd survive their best realistic hit anyway

    return any(bench_mon.hp > active.hp for bench_mon in me.bench)


# ---- SelectType.MAIN --------------------------------------------------------
# Every option here is a fully-specified action (evolve X onto Y, attach card A to
# Pokemon B, use attack C, ...). We rank by action *type* first (build the board
# before attacking), then break ties using card/attack data.

_MAIN_TIER = {
    OptionType.EVOLVE: 0,
    OptionType.PLAY: 1,
    OptionType.ATTACH: 2,
    OptionType.ABILITY: 3,
    OptionType.ATTACK: 4,
    OptionType.RETREAT: 5,
    OptionType.DISCARD: 6,
    OptionType.END: 7,
}

_PLAY_SUBTIER = {
    CardType.POKEMON: 0,
    CardType.SUPPORTER: 1,
    CardType.BASIC_ENERGY: 1,
    CardType.SPECIAL_ENERGY: 1,
    CardType.ITEM: 2,
    CardType.TOOL: 2,
    CardType.STADIUM: 3,
}


def _main_option_key(obs: Observation, opt: Option, urgent_retreat: bool) -> tuple:
    tier = _MAIN_TIER.get(opt.type, 8)
    if opt.type == OptionType.RETREAT and urgent_retreat:
        # Flee a doomed active before attaching/using abilities/attacking with it --
        # but still evolve first if that's on the table, since it might fix the problem.
        tier = 0.5
    sub = 0.0

    if opt.type == OptionType.PLAY:
        card = _hand_card(obs, opt.index)
        sub = _PLAY_SUBTIER.get(card.cardType, 4) if card else 4
        if card is not None and card.cardType == CardType.POKEMON:
            sub -= card.hp / 1000.0  # prefer the sturdier basic, all else equal

    elif opt.type == OptionType.ATTACK:
        state = obs.current
        me = state.players[state.yourIndex]
        opp = state.players[1 - state.yourIndex]
        attacker = me.active[0] if me.active else None
        defender = opp.active[0] if opp.active else None
        attacker_type = _card(attacker.id).energyType if attacker else None
        score = _attack_score(attacker_type, _card(defender.id) if defender else None, _attack(opt.attackId))
        sub = -score  # higher damage sorts first within the ATTACK tier

    elif opt.type == OptionType.EVOLVE:
        sub = 0 if opt.inPlayArea == AreaType.ACTIVE else 1

    return (tier, sub)


def _choose_main(obs: Observation, select: SelectData) -> list[int]:
    urgent_retreat = _retreat_is_urgent(obs)
    best_i = min(range(len(select.option)), key=lambda i: _main_option_key(obs, select.option[i], urgent_retreat))
    return [best_i]


# ---- SelectType.ATTACK -------------------------------------------------------


def _choose_attack(obs: Observation, select: SelectData) -> list[int]:
    state = obs.current
    me = state.players[state.yourIndex]
    opp = state.players[1 - state.yourIndex]
    attacker = me.active[0] if me.active else None
    defender = opp.active[0] if opp.active else None
    attacker_type = _card(attacker.id).energyType if attacker else None
    defender_card = _card(defender.id) if defender else None

    best_i, best_score = 0, -1.0
    for i, opt in enumerate(select.option):
        score = _attack_score(attacker_type, defender_card, _attack(opt.attackId))
        if score > best_score:
            best_i, best_score = i, score
    return [best_i]


# ---- SelectType.EVOLVE ---------------------------------------------------------
# Each option is a fully-specified (source card, target Pokemon) pair. Prefer
# targeting the active Pokemon (immediate board impact) over the bench.


def _choose_target_pair(select: SelectData) -> list[int]:
    def key(opt: Option):
        return 0 if opt.inPlayArea == AreaType.ACTIVE else 1

    best_i = min(range(len(select.option)), key=lambda i: key(select.option[i]))
    return [best_i]


# ---- SelectType.ATTACHED_CARD / CARD_OR_ATTACHED_CARD / ENERGY -----------------
# Every context these three select types carry is some flavor of "give up an
# attached card" (discard, replace, return to hand/deck -- see SelectContext,
# e.g. paying a retreat cost by discarding energy off the retreating active).
# Prefer shedding whichever energy type we hold redundant copies of, so we don't
# gut the one copy of a type the Pokemon actually needs to keep attacking.


def _discard_value(obs: Observation, opt: Option) -> float:
    """Lower = safer to give up."""
    if opt.energyIndex is not None:
        player_index = opt.playerIndex if opt.playerIndex is not None else obs.current.yourIndex
        pokemon = _pokemon_at(obs, player_index, opt.area, opt.index)
        if pokemon is not None and opt.energyIndex < len(pokemon.energies):
            energy_type = pokemon.energies[opt.energyIndex]
            duplicate_count = sum(1 for e in pokemon.energies if e == energy_type)
            return -float(duplicate_count)
        return 0.0
    return _option_hp_value(obs, opt)


def _choose_shed(obs: Observation, select: SelectData) -> list[int]:
    options = select.option
    n = max(select.minCount, min(select.maxCount, len(options)))
    order = sorted(range(len(options)), key=lambda i: _discard_value(obs, options[i]))
    return order[:n] if n > 0 else []


# ---- SelectType.CARD ---------------------------------------------------------
# Generic "pick a card" select used for many different contexts. We special-case
# the common/high-impact ones and fall back to a safe heuristic otherwise.

_KEEP_HIGH_HP_CONTEXTS = {
    SelectContext.SETUP_BENCH_POKEMON,
    SelectContext.TO_BENCH,
    SelectContext.TO_FIELD,
    SelectContext.REMOVE_DAMAGE_COUNTER,
    SelectContext.HEAL,
}

# Bringing a Pokemon into the Active Spot (initial setup, a forced switch, or
# choosing who to retreat into): prefer one that can already attack, not just
# whichever happens to have the most HP.
_PREFER_READY_CONTEXTS = {
    SelectContext.SETUP_ACTIVE_POKEMON,
    SelectContext.SWITCH,
    SelectContext.TO_ACTIVE,
}

_DISCARD_LIKE_CONTEXTS = {
    SelectContext.DISCARD,
    SelectContext.TO_DECK,
    SelectContext.TO_DECK_BOTTOM,
    SelectContext.NOT_MOVE,
}

_TARGET_OPPONENT_CONTEXTS = {
    SelectContext.DAMAGE_COUNTER,
    SelectContext.DAMAGE_COUNTER_ANY,
    SelectContext.DAMAGE,
}


def _resolve_area_card(obs: Observation, player_index: int, area: AreaType | None, index: int | None) -> Card | None:
    """Resolve a CARD-type option's (area, index) to the underlying Card, for the
    areas that expose a flat Card list. ACTIVE/BENCH hold Pokemon, not Card -- use
    _pokemon_at for those instead."""
    if area is None or index is None:
        return None
    player = obs.current.players[player_index]
    if area == AreaType.HAND:
        pool = player.hand
    elif area == AreaType.DISCARD:
        pool = player.discard
    elif area == AreaType.PRIZE:
        pool = player.prize
    elif area == AreaType.DECK:
        pool = obs.select.deck
    elif area == AreaType.STADIUM:
        pool = obs.current.stadium
    else:
        return None
    if pool is None or index >= len(pool):
        return None
    return pool[index]


def _option_card_id(obs: Observation, opt: Option) -> int | None:
    player_index = opt.playerIndex if opt.playerIndex is not None else obs.current.yourIndex
    if opt.area in (AreaType.ACTIVE, AreaType.BENCH):
        pokemon = _pokemon_at(obs, player_index, opt.area, opt.index)
        return pokemon.id if pokemon else None
    card = _resolve_area_card(obs, player_index, opt.area, opt.index)
    return card.id if card else None


def _option_hp_value(obs: Observation, opt: Option) -> float:
    """Current HP for board Pokemon, max HP for Pokemon cards elsewhere, 0 for non-Pokemon."""
    player_index = opt.playerIndex if opt.playerIndex is not None else obs.current.yourIndex
    if opt.area in (AreaType.ACTIVE, AreaType.BENCH):
        pokemon = _pokemon_at(obs, player_index, opt.area, opt.index)
        return float(pokemon.hp) if pokemon else 0.0
    card_id = _option_card_id(obs, opt)
    data = _card(card_id)
    return float(data.hp) if data is not None and data.cardType == CardType.POKEMON else 0.0


def _option_readiness(obs: Observation, opt: Option) -> tuple[int, float]:
    """(can_attack_now, hp). Setup selects have no energy attached yet, so this
    degrades gracefully to a pure HP comparison there."""
    player_index = opt.playerIndex if opt.playerIndex is not None else obs.current.yourIndex
    if opt.area not in (AreaType.ACTIVE, AreaType.BENCH):
        return (0, 0.0)
    pokemon = _pokemon_at(obs, player_index, opt.area, opt.index)
    if pokemon is None:
        return (0, 0.0)
    card = _card(pokemon.id)
    ready = 1 if _affordable_attacks(card, len(pokemon.energies)) else 0
    return (ready, float(pokemon.hp))


def _choose_card(obs: Observation, select: SelectData) -> list[int]:
    context = select.context
    options = select.option
    n = max(select.minCount, min(select.maxCount, len(options)))

    if context in _KEEP_HIGH_HP_CONTEXTS:
        # Prefer our strongest/healthiest Pokemon for setup, switching in, or healing.
        order = sorted(range(len(options)), key=lambda i: -_option_hp_value(obs, options[i]))
        return order[:n] if n > 0 else []

    if context in _PREFER_READY_CONTEXTS:
        # Prefer a Pokemon that can already attack next turn; HP is just the tiebreak.
        order = sorted(range(len(options)), key=lambda i: tuple(-x for x in _option_readiness(obs, options[i])))
        return order[:n] if n > 0 else []

    if context in _DISCARD_LIKE_CONTEXTS:
        # Give up our least valuable card first (lowest HP Pokemon, or non-Pokemon).
        order = sorted(range(len(options)), key=lambda i: _option_hp_value(obs, options[i]))
        return order[:n] if n > 0 else []

    if context in _TARGET_OPPONENT_CONTEXTS:
        # Prefer hitting the opponent's cards over our own; among those, prefer
        # whichever is closest to being knocked out to try to secure the KO.
        my_index = obs.current.yourIndex

        def dmg_key(i):
            opt = options[i]
            is_opponent = opt.playerIndex is not None and opt.playerIndex != my_index
            return (0 if is_opponent else 1, _option_hp_value(obs, opt))

        order = sorted(range(len(options)), key=dmg_key)
        return order[:n] if n > 0 else []

    # Unknown/uncovered context: legal random fallback.
    return random.sample(range(len(options)), n) if n > 0 else []


# ---- SelectType.YES_NO --------------------------------------------------------

_YES_NO_DEFAULT_YES = {
    SelectContext.IS_FIRST,
    SelectContext.ACTIVATE,
    SelectContext.FIRST_EFFECT,
    SelectContext.MULLIGAN,
}


def _choose_yes_no(select: SelectData) -> list[int]:
    want_yes = select.context in _YES_NO_DEFAULT_YES
    for i, opt in enumerate(select.option):
        if (opt.type == OptionType.YES) == want_yes:
            return [i]
    return [0]


# ---- SelectType.COUNT ----------------------------------------------------------


def _choose_count(select: SelectData) -> list[int]:
    best_i = max(range(len(select.option)), key=lambda i: select.option[i].number or 0)
    return [best_i]


# ---- Dispatch -----------------------------------------------------------------


def _decide(obs: Observation) -> list[int]:
    select = obs.select

    if select.type == SelectType.MAIN:
        return _choose_main(obs, select)
    if select.type == SelectType.ATTACK:
        return _choose_attack(obs, select)
    if select.type == SelectType.EVOLVE:
        return _choose_target_pair(select)
    if select.type in (SelectType.ATTACHED_CARD, SelectType.CARD_OR_ATTACHED_CARD, SelectType.ENERGY):
        return _choose_shed(obs, select)
    if select.type == SelectType.CARD:
        return _choose_card(obs, select)
    if select.type == SelectType.YES_NO:
        return _choose_yes_no(select)
    if select.type == SelectType.COUNT:
        return _choose_count(select)

    n = max(select.minCount, min(select.maxCount, len(select.option)))
    return random.sample(range(len(select.option)), n) if n > 0 else []


def agent(obs_dict: dict) -> list[int]:
    obs: Observation = to_observation_class(obs_dict)
    if obs.select is None:
        return read_deck_csv()

    try:
        result = _decide(obs)
        n = len(result)
        if select_ok := (obs.select.minCount <= n <= obs.select.maxCount and len(set(result)) == n):
            return result
    except Exception:
        pass

    # Defensive fallback: the enums/fields above may not cover every case (the game
    # spec notes new enum values can appear during the competition), so never let an
    # unexpected shape crash the agent — fall back to a legal random pick instead.
    select = obs.select
    n = max(select.minCount, min(select.maxCount, len(select.option)))
    return random.sample(range(len(select.option)), n) if n > 0 else []
