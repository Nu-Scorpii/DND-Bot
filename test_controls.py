import sqlite3
import pytest
from ua_bot.controls import parse_command
from ua_bot.dice import prepare, values_of
from ua_bot.errors import RollError
from conftest import ScriptedRNG


def dm(engine, command, request=None):
    return engine.private_message(1, command, is_dm=True, request_id=request)


@pytest.mark.parametrize('text,mode,value,target', [
    ('!Rigger 15', 'force', 15, None), ('!Rigger 0', 'force', 0, None),
    ('!Rigger +3', 'shift', 3, None), ('!Rigger -2', 'shift', -2, None),
    ('!Rigger good', 'good', None, None), ('!Rigger bad', 'bad', None, None),
    ('!Rigger 15 <@!42>', 'force', 15, '42'), ('!Rigger +3 42', 'shift', 3, '42'),
    ('!Rigger force -4', 'force', -4, None), ('!Rigger shift 4', 'shift', 4, None),
    ('!Rigger luck good 42', 'good', None, '42'), ('!Rigger face "Heavy Rain" 42', 'face', 'Heavy Rain', '42')])
def test_simple_command_formats(text, mode, value, target):
    action, setting = parse_command(text)
    assert action == 'set'
    assert (setting['mode'], setting['value'], setting['target']) == (mode, value, target)


@pytest.mark.parametrize('text', ['!Rigger help extra', '!Rigger off 42', '!Rigger status x',
                                 '!Rigger bananas', '!Rigger force', '!Rigger force nope',
                                 '!Rigger luck neutral', '!Rigger 15 0', '!Rigger 15 someone',
                                 '!Rigger 15 42 43', '!Rigger 200000000001', '!Rigger face "unclosed'])
def test_bad_commands_leave_existing_setting_unchanged(text, make_engine):
    engine = make_engine()
    dm(engine, '!Rigger 15')
    before = engine.store.setting(1)
    assert dm(engine, text)
    assert engine.store.setting(1) == before


def test_creator_dm_only_and_no_prefix_false_positives(make_engine):
    engine = make_engine([8])
    assert engine.private_message(2, '!Rigger 15', is_dm=True) is None
    assert engine.private_message(1, '!Rigger 15', is_dm=False) is None
    assert engine.private_message(1, '!Riggerish 15', is_dm=True) is None
    assert engine.private_message(1, 'hello', is_dm=True) is None
    assert engine.roll(2, 100)['dice']['total'] == 8


def test_one_setting_replaces_all_previous_modes_and_off_clears(make_engine):
    engine = make_engine([5, 7, 8])
    dm(engine, '!Rigger good')
    dm(engine, '!Rigger 15')
    assert engine.roll(2, 100)['dice']['total'] == 15
    assert engine.roll(2, 100)['dice']['total'] == 7
    dm(engine, '!Rigger -3')
    dm(engine, '!Rigger off')
    assert engine.roll(2, 100)['dice']['total'] == 8
    assert engine.rng.calls == 3


def test_force_means_final_total_including_bonus_and_modifier(make_engine):
    engine = make_engine([3, 4])
    dm(engine, '!Rigger 15')
    result = engine.roll(2, 100, '2d20+2', bonus=3)['dice']
    assert result['total'] == 15
    assert sum(values_of(result)) == 10
    assert result == prepare('2d20+2', bonus=3).result(values_of(result))
    assert engine.store.setting(1)['mode'] == 'off'


@pytest.mark.parametrize('shift,expected', [('+3', 12), ('-2', 7), ('+0', 9)])
def test_shift_applies_exactly_once(shift, expected, make_engine):
    engine = make_engine([5, 8])
    dm(engine, '!Rigger ' + shift)
    assert engine.roll(2, 100, '1d20+2', bonus=2)['dice']['total'] == expected
    assert engine.roll(2, 200)['dice']['total'] == 8


@pytest.mark.parametrize('mode,expected', [('good', 30), ('bad', -8)])
def test_full_luck_respects_signed_total_and_kept_dice(mode, expected, make_engine):
    engine = make_engine([2, 9, 1, 7])
    dm(engine, '!Rigger ' + mode)
    assert engine.roll(2, 100, '1d20-1d20+10', bonus=1)['dice']['total'] == expected
    engine.roll(2, 200, '2d20', keep='high')
    assert engine.store.setting(1)['mode'] == mode
    assert engine.rng.calls == 4


def test_target_follows_user_across_servers_not_guild_ids(make_engine):
    engine = make_engine([3, 4, 5, 6])
    dm(engine, '!Rigger 17 42')
    assert engine.roll(9, 100)['dice']['total'] == 3
    assert engine.store.setting(1)['mode'] == 'force'
    assert engine.roll(42, 200)['dice']['total'] == 17
    assert engine.roll(42, 100)['dice']['total'] == 5
    assert engine.roll(42, 200)['dice']['total'] == 6


@pytest.mark.parametrize('command,target', [('!Rigger 10', 10), ('!Rigger +7', 7)])
def test_custom_numeric_faces_and_gaps(command, target, make_engine):
    engine = make_engine([0, 0])
    dm(engine, command)
    faces = {'a': [0, 6, 7], 'b': [0, 3, 11]}
    result = engine.roll(2, 100, '1d[a]+1d[b]', faces=faces)
    assert result['notice'] is None
    assert result['dice']['total'] == target
    assert result['dice'] == prepare('1d[a]+1d[b]', faces=faces).result(values_of(result['dice']))


@pytest.mark.parametrize('command,expression,faces,natural', [
    ('!Rigger 3', '1d[custom]', [0, 5], 0),
    ('!Rigger +1', '1d[custom]', [0, 5], 5),
    ('!Rigger 100', '1d6', None, 4),
    ('!Rigger 15', '1d[custom]', ['sun', 'rain'], 'rain'),
    ('!Rigger face storm', '1d[custom]', ['sun', 'rain'], 'sun')])
def test_impossible_or_incompatible_control_never_breaks_roll(command, expression, faces, natural, make_engine):
    engine = make_engine([natural])
    dm(engine, command)
    outcome = engine.roll(2, 100, expression, faces=faces)
    assert values_of(outcome['dice']) == [natural]
    assert outcome['notice'] and 'stayed normal' in outcome['notice']
    assert engine.store.setting(1)['mode'] == 'off'
    assert 'could not apply' in dm(engine, '!Rigger status')


def test_text_face_control_preserves_case_and_spaces(make_engine):
    engine = make_engine(['Sun', 'Sun'])
    dm(engine, '!Rigger face "Heavy Rain"')
    outcome = engine.roll(2, 100, faces=['Sun', 'Heavy Rain'])
    assert values_of(outcome['dice']) == ['Heavy Rain']
    assert outcome['dice']['total'] is None
    assert outcome['notice'] is None
    assert values_of(engine.roll(2, 100, faces=['Sun', 'Heavy Rain'])['dice']) == ['Sun']


def test_luck_skips_text_without_eating_random_draw_or_breaking_numeric_roll(make_engine):
    engine = make_engine(['rain', 3])
    dm(engine, '!Rigger good')
    assert engine.roll(2, 100, faces=['sun', 'rain'])['dice']['total'] is None
    assert engine.roll(2, 200)['dice']['total'] == 20
    assert engine.rng.calls == 2


def test_invalid_roll_and_invalid_json_dont_consume_one_use_control(make_engine):
    engine = make_engine([4])
    dm(engine, '!Rigger 15')
    for expression, faces in [('101d6', None), ('1d[custom]', '{broken}'), ('1d0', None)]:
        with pytest.raises(RollError):
            engine.roll(2, 100, expression, faces=faces)
    assert engine.rng.calls == 0
    assert engine.store.setting(1)['mode'] == 'force'
    assert engine.roll(2, 100)['dice']['total'] == 15


def test_duplicate_roll_returns_same_result_without_consuming_new_setting(make_engine):
    engine = make_engine([3, 4])
    dm(engine, '!Rigger 15')
    first = engine.roll(2, 100, request_id=900)
    dm(engine, '!Rigger +2')
    replay = engine.roll(2, 100, request_id=900)
    assert replay['dice'] == first['dice']
    assert replay['replayed'] is True
    assert engine.store.setting(1)['mode'] == 'shift'
    assert engine.roll(2, 100, request_id=901)['dice']['total'] == 6
    assert engine.rng.calls == 2


def test_receipt_collision_cannot_expose_another_users_result(make_engine):
    engine = make_engine([4])
    engine.roll(2, 100, request_id=900)
    with pytest.raises(RollError, match='different command'):
        engine.roll(3, 200, request_id=900)
    assert engine.rng.calls == 1


def test_duplicate_dm_does_not_rearm_used_setting(make_engine):
    engine = make_engine([4, 7])
    first_reply = dm(engine, '!Rigger 15', 700)
    engine.roll(2, 100)
    assert dm(engine, '!Rigger 15', 700) == first_reply
    assert engine.store.setting(1)['mode'] == 'off'
    assert engine.roll(2, 100)['dice']['total'] == 7


def test_restart_keeps_target_and_used_once_state(tmp_path, make_engine):
    path = tmp_path / 'dice.sqlite3'
    first = make_engine([], path)
    dm(first, '!Rigger 15 42')
    second = make_engine([4], path)
    assert second.roll(42, 200, request_id=500)['dice']['total'] == 15
    third = make_engine([], path)
    assert third.store.setting(1)['mode'] == 'off'
    assert third.roll(42, 200, request_id=500)['dice']['total'] == 15
    assert third.rng.calls == 0


def test_old_campaign_data_and_broken_controls_are_never_loaded(tmp_path, make_engine):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE campaigns (guild TEXT PRIMARY KEY, state TEXT)')
        db.execute('INSERT INTO campaigns VALUES (?,?)', ('100', 'not even valid JSON'))
        db.execute('CREATE TABLE simple_controls (owner TEXT PRIMARY KEY, state TEXT)')
        db.execute('INSERT INTO simple_controls VALUES (?,?)', ('1', '{"next":{"mode":"force","value":999}}'))
    engine = make_engine([8], path)
    assert engine.roll(2, 100)['dice']['total'] == 8
    assert engine.store.db.execute('SELECT state FROM campaigns').fetchone()['state'] == 'not even valid JSON'


def test_transaction_failure_restores_control_and_receipt(make_engine, monkeypatch):
    engine = make_engine([4, 5])
    dm(engine, '!Rigger 15')
    original = engine.store.remember
    def fail(*args):
        raise sqlite3.OperationalError('test write failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        engine.roll(2, 100, request_id=901)
    assert engine.store.setting(1)['mode'] == 'force'
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0
    monkeypatch.setattr(engine.store, 'remember', original)
    assert engine.roll(2, 100, request_id=901)['dice']['total'] == 15


def test_changing_creator_id_does_not_inherit_previous_setting(make_engine):
    engine = make_engine([4])
    dm(engine, '!Rigger good')
    engine.owner_id = 2
    assert engine.roll(3, 100)['dice']['total'] == 4


def test_corrupt_new_setting_falls_back_to_normal_roll(make_engine):
    engine = make_engine([4])
    engine.store.db.execute('INSERT INTO dice_settings_v3 VALUES (?,?)', ('1', '{"mode":"force","value":"invalid"}'))
    assert engine.roll(2, 100)['dice']['total'] == 4


def test_receipt_cache_stays_bounded(make_engine):
    engine = make_engine([1] * 1005)
    for request in range(1005):
        engine.roll(2, 100, '1d1', request_id=request)
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 1000
