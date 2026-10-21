from itertools import product
import random
import pytest
from ua_bot.dice import prepare, values_of
from ua_bot.solver import Unavailable, bounds, force_total


CASES = [
    ('2d3+1', None, 'all', -2),
    ('1d3-2d2+3', None, 'all', 2),
    ('2d[custom]+1', [0, 2, 5], 'all', 2),
    ('1d[a]-2d[b]-1', {'a': [-4, 0, 3], 'b': [-2, 1, 5]}, 'all', -1),
    ('1d[a]+1d[b]+1d3', {'a': [0, 6, 7], 'b': [0, 3, 11]}, 'all', 1),
    ('3d[custom]+2', [-5, 0, 8], 'high', 3),
    ('3d[custom]-2', [-5, 0, 8], 'low', -1),
    ('3d3', None, 'high', 0),
    ('3d3', None, 'low', 0),
    ('2d[custom]-1d2', [-1, 0, 1], 'all', 0),
]


@pytest.mark.parametrize('expression,faces,keep,bonus', CASES)
def test_exact_solver_against_every_reachable_small_total(expression, faces, keep, bonus):
    plan = prepare(expression, faces=faces, keep=keep, bonus=bonus)
    domains = [list(dict.fromkeys(die.faces)) if die.faces else list(range(1, die.sides + 1)) for _, die in plan.dice]
    originals = list(product(*domains))
    totals = {plan.result(values)['total'] for values in originals}
    low, high = bounds(plan)
    assert (low, high) == (min(totals), max(totals))
    # Different natural rolls exercise preservation and custom-face backtracking.
    for original in (originals[0], originals[len(originals) // 2], originals[-1]):
        natural = plan.result(original)
        for target in range(low - 1, high + 2):
            if target not in totals:
                with pytest.raises(Unavailable):
                    force_total(plan, natural, target)
            else:
                result = force_total(plan, natural, target)
                assert result['total'] == target
                assert result == plan.result(values_of(result))
            assert values_of(natural) == list(original)


def test_large_numbered_dice_allocated_without_enumerating_faces():
    plan = prepare('50d1000000000-50d1000000000+3', bonus=4)
    natural = plan.roll(random.Random(7))
    for target in (-49_999_999_943, 7, 49_999_999_957):
        result = force_total(plan, natural, target)
        assert result['total'] == target
        assert result == plan.result(values_of(result))


def test_custom_backtracking_finds_total_that_simple_greedy_misses():
    plan = prepare('1d[a]+1d[b]', faces={'a': [0, 6, 7], 'b': [0, 3, 11]})
    assert force_total(plan, plan.result([0, 0]), 10)['total'] == 10


def test_search_limit_fails_explicitly_and_does_not_change_original():
    plan = prepare('2d[custom]', faces=[0, 2, 5])
    natural = plan.result([0, 0])
    with pytest.raises(Unavailable, match='search limit'):
        force_total(plan, natural, 10, budget=1)
    assert values_of(natural) == [0, 0]
