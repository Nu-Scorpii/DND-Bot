"""Set an exact total using legal faces. Failure leaves the ordinary roll intact."""

from .dice import values_of

SEARCH_BUDGET = 200_000


class Unavailable(ValueError):
    pass


def bounds(plan):
    ranges = [(min(die.faces), max(die.faces)) if die.faces is not None else (1, die.sides)
              for _, die in plan.dice]
    if plan.keep != 'all':
        low, high = ranges[0]
        return low + plan.modifier, high + plan.modifier
    return (plan.modifier + sum(low if sign > 0 else -high for (sign, _), (low, high) in zip(plan.dice, ranges)),
            plan.modifier + sum(high if sign > 0 else -low for (sign, _), (low, high) in zip(plan.dice, ranges)))


def force_total(plan, natural, target, *, budget=SEARCH_BUDGET):
    if not plan.numeric:
        raise Unavailable('text dice have no numeric total')
    low, high = bounds(plan)
    if not low <= target <= high:
        raise Unavailable(f'the legal total range is {low}–{high}')
    if natural['total'] == target:
        return natural
    values = values_of(natural)
    if plan.keep != 'all':
        face = target - plan.modifier
        die = plan.dice[0][1]
        if not die.contains(face):
            raise Unavailable('that kept result is not a face on this custom die')
        values[0] = face
        for index in range(1, len(values)):
            values[index] = min(values[index], face) if plan.keep == 'high' else max(values[index], face)
        return plan.result(values)

    # Continuous numbered dice (and contiguous custom faces) can be allocated
    # directly, regardless of size. Only custom dice with gaps need a search.
    continuous, custom = [], []
    for index, ((sign, die), value) in enumerate(zip(plan.dice, values)):
        domain = sorted({sign * face for face in die.faces}) if die.faces is not None else None
        lo, hi = (domain[0], domain[-1]) if domain is not None else ((1, die.sides) if sign > 0 else (-die.sides, -1))
        if domain is None or len(domain) == hi - lo + 1:
            continuous.append((index, sign, lo, hi, sign * value))
        else:
            preferred = sign * value
            custom.append((index, sign, tuple(sorted(domain, key=lambda x: (abs(x - preferred), x))), lo, hi))
    cont_low = sum(item[2] for item in continuous)
    cont_high = sum(item[3] for item in continuous)
    target -= plan.modifier
    suffix_low, suffix_high = [0] * (len(custom) + 1), [0] * (len(custom) + 1)
    for index in range(len(custom) - 1, -1, -1):
        suffix_low[index] = suffix_low[index + 1] + custom[index][3]
        suffix_high[index] = suffix_high[index + 1] + custom[index][4]
    failed, work = set(), 0

    def search(index, subtotal):
        nonlocal work
        if index == len(custom):
            return [] if cont_low <= target - subtotal <= cont_high else None
        state = (index, subtotal)
        if state in failed:
            return None
        minimum = target - subtotal - suffix_high[index + 1] - cont_high
        maximum = target - subtotal - suffix_low[index + 1] - cont_low
        for candidate in custom[index][2]:
            work += 1
            if work > budget:
                raise Unavailable('this custom-face combination exceeds the safe search limit; try fewer dice')
            if not minimum <= candidate <= maximum:
                continue
            rest = search(index + 1, subtotal + candidate)
            if rest is not None:
                return [candidate] + rest
        failed.add(state)
        return None

    chosen = search(0, 0)
    if chosen is None:
        raise Unavailable('that total cannot be made from these custom faces')
    for item, contribution in zip(custom, chosen):
        values[item[0]] = item[1] * contribution
    allocated = [item[4] for item in continuous]
    remaining = target - sum(chosen) - sum(allocated)
    for index, item in enumerate(continuous):
        move = min(remaining, item[3] - allocated[index]) if remaining >= 0 else max(remaining, item[2] - allocated[index])
        allocated[index] += move
        remaining -= move
        values[item[0]] = item[1] * allocated[index]
    assert remaining == 0
    result = plan.result(values)
    assert result['total'] == target + plan.modifier
    return result


def force_face(plan, natural, face):
    if len(plan.dice) != 1 or plan.keep != 'all' or len(plan.terms) != 1 or plan.dice[0][0] != 1 or plan.bonus:
        raise Unavailable('face forcing needs one custom die, without arithmetic')
    die = plan.dice[0][1]
    if die.faces is None or not die.contains(face):
        raise Unavailable('that text label is not a face on this custom die')
    return plan.result([face])
