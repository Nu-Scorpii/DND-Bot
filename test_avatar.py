import asyncio
import sqlite3
from unittest.mock import AsyncMock
import pytest
from ua_bot.client import DiceBot
from ua_bot.controls import parse_command
from ua_bot.display import length
from ua_bot.errors import RollError
from test_controls import dm
from test_discord import interaction


def draws(nation, detail=1, bending=61, end=1, strength=12):
    return [11, 1, 2, 3, 4, 5, 6, nation] + ([detail] if nation <= 75 else []) + [bending] + (
        [end] if bending <= 60 or nation > 75 else []) + ([strength] if bending > 60 else [])


@pytest.mark.parametrize('nation,expected,branch', [
    (1, 'Fire Nation', 'family'), (25, 'Fire Nation', 'family'),
    (26, 'Water Tribes', 'tribe'), (50, 'Water Tribes', 'tribe'),
    (51, 'Earth Kingdom', 'control'), (75, 'Earth Kingdom', 'control'),
    (76, 'Nomad', 'element'), (100, 'Nomad', 'element')])
def test_nation_boundaries_and_only_applicable_draws(nation, expected, branch, make_engine):
    engine = make_engine(draws(nation))
    character = engine.avatar(2, 100)['character']
    assert character['iq'] == 11
    assert character['stats'] == [1, 2, 3, 4, 5, 6]
    assert character['nation'] == expected
    assert set(character['rolls']) == {'iq', 'stats', 'nation', 'bending', branch, 'bending_strength'}
    assert character['bending_strength'] == 12
    assert engine.rng.calls == 11


@pytest.mark.parametrize('value,expected', [(1, 'Civilian family'), (49, 'Civilian family'),
    (50, 'Military family'), (79, 'Military family'), (80, 'High ranking family'),
    (94, 'High ranking family'), (95, 'Royal family'), (100, 'Royal family')])
def test_fire_family_boundaries(value, expected, make_engine):
    assert make_engine(draws(1, detail=value)).avatar(2, 100)['character']['family'] == expected


@pytest.mark.parametrize('value,expected', [(1, 'Southern tribe'), (2, 'Northern tribe'), (3, 'Swamp tribe')])
def test_water_tribes(value, expected, make_engine):
    assert make_engine(draws(26, detail=value)).avatar(2, 100)['character']['tribe'] == expected


@pytest.mark.parametrize('value,expected', [(1, True), (15, True), (16, False), (20, False)])
def test_earth_control_boundaries(value, expected, make_engine):
    assert make_engine(draws(51, detail=value)).avatar(2, 100)['character']['fire_control'] is expected


@pytest.mark.parametrize('nation,element', [(1, 'Fire'), (26, 'Water'), (51, 'Earth'), (76, 'Fire')])
@pytest.mark.parametrize('value,bender', [(1, False), (59, False), (60, False), (61, True), (100, True)])
def test_bending_threshold_and_nation_element(nation, element, value, bender, make_engine):
    character = make_engine(draws(nation, bending=value)).avatar(2, 100)['character']
    assert character['bender'] is bender
    assert character['bending'] == (element + ' bending' if bender else None)
    assert ('combat' in character['rolls']) is (not bender)
    assert ('element' in character['rolls']) is (bender and nation == 76)


@pytest.mark.parametrize('value,element', [(1, 'Fire bending'), (2, 'Water bending'), (3, 'Earth bending')])
def test_nomads_have_only_three_elements(value, element, make_engine):
    character = make_engine(draws(76, end=value)).avatar(2, 100)['character']
    assert character['nation'] == 'Nomad'
    assert character['bending'] == element
    assert character['tribe'] is character['family'] is character['fire_control'] is None


@pytest.mark.parametrize('value,style', [(1, 'Sword'), (20, 'Sword'), (21, 'Bow'), (40, 'Bow'),
    (41, 'Fan'), (60, 'Fan'), (61, 'Fists'), (80, 'Fists'), (81, 'Pressure point'), (100, 'Pressure point')])
def test_non_bender_combat_boundaries(value, style, make_engine):
    character = make_engine(draws(1, bending=59, end=value)).avatar(2, 100)['character']
    assert character['combat'] == style
    assert character['bending'] is None


@pytest.mark.parametrize('text,part,mode,value,target', [
    ('!Rigger nation 30', 'nation', 'force', 30, None),
    ('!Rigger avatar bending 100 42', 'bending', 'force', 100, '42'),
    ('!Rigger IQ +3 <@42>', 'iq', 'shift', 3, '42'),
    ('!Rigger stats good', 'stats', 'good', None, None),
    ('!Rigger combat bad', 'combat', 'bad', None, None),
    ('!Rigger stats force 120', 'stats', 'force', 120, None)])
def test_part_control_syntax(text, part, mode, value, target):
    action, setting = parse_command(text)
    assert action == 'set'
    assert (setting['part'], setting['mode'], setting['value'], setting['target']) == (part, mode, value, target)


@pytest.mark.parametrize('text', ['!Rigger avatar', '!Rigger avatar banana 20', '!Rigger nation',
    '!Rigger nation 0', '!Rigger nation 101', '!Rigger iq 21', '!Rigger stats 5', '!Rigger stats 121',
    '!Rigger tribe 4', '!Rigger element 4', '!Rigger combat face Sword', '!Rigger nation off',
    '!Rigger bending status', '!Rigger family 100 42 43'])
def test_invalid_part_control_preserves_active_setting(text, make_engine):
    engine = make_engine()
    dm(engine, '!Rigger 15')
    before = engine.store.setting(1)
    assert dm(engine, text)
    assert engine.store.setting(1) == before
    assert engine.rng.calls == 0


@pytest.mark.parametrize('nation,detail,expected', [(1, 95, 'Fire Nation'), (30, 3, 'Water Tribes'),
    (55, 16, 'Earth Kingdom'), (99, None, 'Nomad')])
def test_forced_nation_drives_subsequent_branch(nation, detail, expected, make_engine):
    scripted = [11, 1, 2, 3, 4, 5, 6, 80] + ([detail] if detail is not None else []) + [100] + (
        [3] if expected == 'Nomad' else []) + [12]
    engine = make_engine(scripted)
    dm(engine, f'!Rigger nation {nation}')
    outcome = engine.avatar(2, 100)
    assert outcome['character']['nation'] == expected
    assert outcome['character']['rolls']['nation']['total'] == nation
    assert outcome['notice'] is None
    assert engine.store.setting(1)['mode'] == 'off'


@pytest.mark.parametrize('forced,natural,end,bender', [(100, 59, None, True), (1, 100, 81, False)])
def test_forced_bending_selects_branch_without_unused_draws(forced, natural, end, bender, make_engine):
    scripted = [11, 1, 2, 3, 4, 5, 6, 1, 1, natural] + ([12] if bender else [end])
    engine = make_engine(scripted)
    dm(engine, f'!Rigger bending {forced}')
    character = engine.avatar(2, 100)['character']
    assert character['bender'] is bender
    assert character['combat'] == (None if bender else 'Pressure point')
    assert engine.rng.calls == len(scripted)


def test_scoped_control_ignores_roll_and_other_players_then_follows_target(make_engine):
    engine = make_engine([4] + draws(1) + draws(1))
    dm(engine, '!Rigger bending 1 42')
    assert engine.roll(42, 200)['dice']['total'] == 4
    assert engine.avatar(9, 100)['character']['bender'] is True
    assert engine.store.setting(1)['mode'] == 'force'
    # Add a combat draw only to the character whose bending check is changed.
    engine.rng.values = iter(draws(1)[:-1] + [100])
    assert engine.avatar(42, 300)['character']['combat'] == 'Pressure point'
    assert engine.store.setting(1)['mode'] == 'off'


def test_bare_one_use_applies_only_to_iq_then_rolls_normally(make_engine):
    engine = make_engine(draws(26) + [7])
    dm(engine, '!Rigger 20')
    character = engine.avatar(2, 100)['character']
    assert character['iq'] == 20 and character['stats'] == [1, 2, 3, 4, 5, 6]
    assert engine.roll(2, 100)['dice']['total'] == 7


def test_stats_force_total_has_six_legal_faces(make_engine):
    engine = make_engine(draws(1))
    dm(engine, '!Rigger stats 120')
    character = engine.avatar(2, 100)['character']
    assert character['stats'] == [20] * 6
    assert character['rolls']['stats']['total'] == 120
    assert engine.rng.calls == 11


def test_shift_is_exact_and_impossible_shift_stays_normal(make_engine):
    engine = make_engine(draws(1) + draws(1))
    dm(engine, '!Rigger iq +3')
    assert engine.avatar(2, 100)['character']['iq'] == 14
    dm(engine, '!Rigger iq +100')
    outcome = engine.avatar(2, 100)
    assert outcome['character']['iq'] == 11
    assert 'stayed normal' in outcome['notice']
    assert engine.store.setting(1)['mode'] == 'off'


@pytest.mark.parametrize('mode,expected', [('good', 20), ('bad', 1)])
def test_full_part_luck_changes_only_that_part_and_persists(mode, expected, make_engine):
    engine = make_engine([4, 1, 2, 3, 4, 5, 6, 1, 1, 61, 12, 7])
    dm(engine, f'!Rigger iq {mode}')
    assert engine.avatar(2, 100)['character']['iq'] == expected
    assert engine.roll(2, 200)['dice']['total'] == 7
    assert engine.rng.calls == 12
    assert engine.store.setting(1)['mode'] == mode
    assert engine.store.setting(1)['part'] == 'iq'


def test_skipped_one_use_part_clears_privately_without_extra_draw(make_engine):
    engine = make_engine(draws(1))
    dm(engine, '!Rigger tribe 3')
    outcome = engine.avatar(2, 100)
    assert outcome['character']['tribe'] is None and engine.rng.calls == 11
    assert 'tribe was not rolled' in outcome['notice']
    assert engine.store.setting(1)['mode'] == 'off'
    assert 'tribe was not rolled' in dm(engine, '!Rigger status')


def test_skipped_lasting_part_stays_enabled(make_engine):
    engine = make_engine(draws(1))
    dm(engine, '!Rigger tribe good')
    assert engine.avatar(2, 100)['notice'] is None
    assert engine.store.setting(1)['mode'] == 'good'


def test_avatar_replay_returns_whole_character_without_consuming_new_setting(make_engine):
    engine = make_engine(draws(1))
    dm(engine, '!Rigger iq 20')
    first = engine.avatar(2, 100, request_id=902)
    dm(engine, '!Rigger nation 30')
    replay = engine.avatar(2, 100, request_id=902)
    assert replay['character'] == first['character'] and replay['replayed']
    assert engine.rng.calls == 11 and engine.store.setting(1)['part'] == 'nation'
    with pytest.raises(RollError, match='different command'):
        engine.avatar(3, 200, request_id=902)
    assert engine.rng.calls == 11


def test_avatar_write_failure_rolls_back_control_and_all_receipts(make_engine, monkeypatch):
    engine = make_engine(draws(1) + draws(1))
    dm(engine, '!Rigger iq 20')
    before = engine.store.setting(1)
    remember = engine.store.remember
    def fail(*args):
        raise sqlite3.OperationalError('test failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        engine.avatar(2, 100, request_id=904)
    assert engine.store.setting(1) == before
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0
    monkeypatch.setattr(engine.store, 'remember', remember)
    assert engine.avatar(2, 100, request_id=904)['character']['iq'] == 20


def test_old_v3_setting_is_preserved_without_new_field(tmp_path, make_engine):
    path = tmp_path / 'v3.sqlite3'
    old = make_engine([], path)
    old.store.save_setting(1, {'mode': 'force', 'value': 20, 'target': '42', 'last': 'previous roll'})
    upgraded = make_engine(draws(1), path)
    assert upgraded.store.setting(1)['part'] is None
    assert upgraded.store.setting(1)['last'] == 'previous roll'
    assert upgraded.avatar(42, 200)['character']['iq'] == 20


def test_avatar_part_and_receipt_persist_across_restart(tmp_path, make_engine):
    path = tmp_path / 'avatar.sqlite3'
    first = make_engine([], path)
    dm(first, '!Rigger bending 100 42')
    second = make_engine(draws(1, bending=59)[:-1] + [12], path)
    result = second.avatar(42, 200, request_id=905)
    assert result['character']['bender']
    third = make_engine([], path)
    assert third.avatar(42, 200, request_id=905)['character'] == result['character']
    assert third.rng.calls == 0 and third.store.setting(1)['mode'] == 'off'


def test_avatar_discord_reply_is_safe_and_private_notice_is_not_public(make_engine):
    async def check():
        engine = make_engine(draws(1))
        dm(engine, '!Rigger tribe 3')
        bot = DiceBot(engine)
        bot.notify_creator = AsyncMock()
        event = interaction()
        event.user.display_name = '@everyone **Hero**'
        await bot.run_avatar(event)
        event.response.defer.assert_awaited_once_with(thinking=True)
        public = event.edit_original_response.await_args.kwargs['content']
        assert 'Avatar character' in public and '**IQ:** `11`' in public
        assert '**Stats:** `1` · `2` · `3` · `4` · `5` · `6`' in public
        assert 'Fire bending' in public and 'Rigger' not in public and 'tribe was not rolled' not in public
        assert '@everyone' not in public and length(public) <= 2000
        bot.notify_creator.assert_awaited_once()
        await bot.close()
    asyncio.run(check())


def test_avatar_dm_is_private_error_without_consuming_setting(make_engine):
    async def check():
        engine = make_engine()
        dm(engine, '!Rigger nation 30')
        bot = DiceBot(engine)
        event = interaction()
        event.guild_id = None
        await bot.run_avatar(event)
        event.response.defer.assert_not_awaited()
        assert event.response.send_message.await_args.kwargs['ephemeral']
        assert engine.rng.calls == 0 and engine.store.setting(1)['part'] == 'nation'
        await bot.close()
    asyncio.run(check())
