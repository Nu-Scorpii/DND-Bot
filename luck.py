"""Private extra-die selection and percentage luck; public results stay ordinary."""

from .dice import values_of


def extra_dice_result(plan, natural, mode, extra, rng):
    """Keep one sampled face per original die; never return discarded candidates."""
    selected = []
    for (_, die), original in zip(plan.dice, values_of(natural)):
        if die.numeric:
            rank = lambda value: value
        else:
            # Text/mixed dice use first-occurrence order. Repetitions add weight,
            # without changing a face's rank or comparing unlike Python types.
            order = {}
            for face in die.faces:
                order.setdefault((type(face), face), len(order))
            rank = lambda value: order[(type(value), value)]
        winner = original
        for _ in range(extra):
            candidate = die.roll(rng)
            if (rank(candidate) > rank(winner) if mode == 'extra_high' else rank(candidate) < rank(winner)):
                winner = candidate
        selected.append(winner)
    return plan.result(selected)


def luck_result(plan, natural, mode, percent):
    if not plan.numeric:
        return natural
    values = []
    for (sign, die), value in zip(plan.dice, values_of(natural)):
        up = (mode == 'good') == (sign > 0)
        low, high = (1, die.sides) if die.sides is not None else (min(die.faces), max(die.faces))
        extreme = high if up else low
        # Integer arithmetic avoids rounding errors on large or negative dice.
        scaled = value * 100 + (extreme - value) * percent
        if die.sides is not None:
            move = (abs(extreme - value) * percent + 50) // 100
            adjusted = value + move if up else value - move
        else:
            adjusted = min(set(die.faces), key=lambda face: (abs(face * 100 - scaled), -face if up else face))
        values.append(adjusted)
    return plan.result(values)
