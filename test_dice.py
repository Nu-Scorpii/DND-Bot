import pytest
from ua_bot.dice import custom_faces, prepare, values_of
from ua_bot.errors import RollError
from conftest import ScriptedRNG


def test_defaults_and_inline_custom_default():
    assert prepare().expression == '1d20'
    assert prepare(faces='[0,1,1,5]').expression == '1d[custom]'
    assert prepare(faces={'weather': ['sun', 'rain']}).expression == '1d[weather]'


def test_mixed_dice_signed_modifiers_and_bonus():
    result = prepare('2D17 + 1d8 − 3', bonus=4).roll(ScriptedRNG([10, 7, 2]))
    assert values_of(result) == [10, 7, 2]
    assert result['total'] == 20
    assert result['bonus'] == 4


def test_weighted_faces_preserved_without_saved_dice():
    rng = ScriptedRNG([0, 1, 1, 5])
    plan = prepare(faces=[0, 1, 1, 5])
    assert [plan.roll(rng)['total'] for _ in range(4)] == [0, 1, 1, 5]
    assert rng.domains == [(0, 1, 1, 5)] * 4


def test_multiple_named_inline_custom_dice_and_negative_faces():
    result = prepare('1d[a]-2d[b]+1d3-2', faces={'a': [-5, 8], 'b': [-3, 0, 2]}, bonus=-1).roll(ScriptedRNG([8, -3, 2, 3]))
    assert result['total'] == 9


@pytest.mark.parametrize('keep,expected', [('all', 22), ('high', 16), ('low', 2)])
def test_keep_adds_modifier_and_bonus_once(keep, expected):
    assert prepare('3d20+2', keep=keep, bonus=-1).roll(ScriptedRNG([1, 5, 15]))['total'] == expected


def test_text_and_mixed_faces_always_have_no_total():
    plan = prepare('1d[custom]+1d6', faces=[7, 'Storm'])
    assert plan.roll(ScriptedRNG([7, 3]))['total'] is None
    assert plan.roll(ScriptedRNG(['Storm', 3]))['total'] is None


@pytest.mark.parametrize('sides', [1, 3, 17, 1_000_000_000])
def test_arbitrary_numbered_sides(sides):
    plan = prepare(f'1d{sides}')
    assert plan.roll(ScriptedRNG([sides]))['total'] == sides
    assert not plan.dice[0][1].contains(True)


def test_maximum_dice_and_terms():
    assert prepare('100d1').roll(ScriptedRNG([1] * 100))['total'] == 100
    assert prepare('1d1' + '+1' * 19).roll(ScriptedRNG([1]))['total'] == 20


@pytest.mark.parametrize('expression', ['', ' ', '7', '0d20', '101d6', '1d0', '1d1000000001',
                                          '1d6 1d8', '1d6++2', '1d6+1.5', '1d6*2', '1d6;',
                                          '1d[missing]', '1d6+1000000001', '1d6' + '+1' * 20, 'd' * 501])
def test_invalid_expressions(expression):
    with pytest.raises(RollError):
        prepare(expression)


@pytest.mark.parametrize('source', ['{bad}', '[]', '{}', 'true', '[true]', '[1.5]', '[null]',
                                    '[[1]]', '["a\\nb"]', [1_000_000_001], {'BAD': [1]},
                                    {'a': []}, [1] * 1001, ['x' * 41], 'a' * 20_001])
def test_invalid_faces(source):
    with pytest.raises(RollError):
        custom_faces(source)


@pytest.mark.parametrize('changes', [{'bonus': 1}, {'keep': 'high'}, {'expression': '1d[custom]+0'},
                                      {'expression': '-1d[custom]'}, {'expression': '1d[custom]-1d6'}])
def test_text_arithmetic_rejected_before_random_draw(changes, make_engine):
    changes = dict(changes)
    engine = make_engine([3])
    engine.private_message(1, '!Rigger 15', is_dm=True)
    expression = changes.pop('expression', '1d[custom]')
    with pytest.raises(RollError):
        engine.roll(2, 100, expression, faces=[7, 'rain'], **changes)
    assert engine.rng.calls == 0
    assert engine.store.setting(1)['mode'] == 'force'


@pytest.mark.parametrize('kwargs', [{'bonus': True}, {'bonus': 1.2}, {'bonus': 1_000_000_001},
                                     {'keep': 'wrong'}, {'expression': '-2d20', 'keep': 'high'},
                                     {'expression': '1d6+1d8', 'keep': 'low'}])
def test_invalid_roll_options(kwargs):
    with pytest.raises(RollError):
        prepare(**kwargs)
