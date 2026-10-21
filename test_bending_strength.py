"""Conditional strength, real rig integration, and private/public UI regressions."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ua_bot.client import DiceBot
from ua_bot.dice import prepare, values_of
from ua_bot.display import avatar_messages, length
from ua_bot.errors import RollError
from ua_bot.rigger_ui import PanelInput, RiggerPanel
from test_avatar import draws
from test_discord import interaction
from test_rigger_panel import button, owner_event, select


def arm(engine, mode='force', value=18, *, uses=2, target=42, part='bending_strength'):
    return engine.configure(1, is_dm=True, mode=mode, value=value, target=target,
                            scope='avatar', part=part, uses=uses)


def active(engine):
    return engine.control_status(1, is_dm=True)['rules']


@pytest.mark.parametrize('nation,element,end', [
    (1, 'Fire', 1), (26, 'Water', 1), (51, 'Earth', 1),
    (76, 'Fire', 1), (76, 'Water', 2), (76, 'Earth', 3)])
def test_every_kind_of_bender_gets_one_separate_strength_die(nation, element, end, make_engine):
    script = draws(nation, bending=61, end=end, strength=17)
    engine = make_engine(script)
    char = engine.avatar(42, 100)['character']
    assert char['bending'] == element + ' bending'
    assert char['bending_strength'] == 17
    assert char['rolls']['bending_strength'] == prepare('1d20').result([17])
    assert char['stats'] == [1, 2, 3, 4, 5, 6]  # This is a seventh, separate stat.
    assert char['iq'] == 11 and engine.rng.calls == len(script)
    assert list(char['rolls'])[-1] == 'bending_strength'
    public = '\n'.join(avatar_messages(char))
    assert public.count('**Bending Strength:** `17` (1d20)') == 1


@pytest.mark.parametrize('nation', [1, 26, 51, 76])
def test_nonbenders_at_sixty_have_no_strength_draw_or_display(nation, make_engine):
    script = draws(nation, bending=60, end=81)
    engine = make_engine(script)
    char = engine.avatar(42, 100)['character']
    assert not char['bender'] and char['bending_strength'] is None
    assert 'bending_strength' not in char['rolls']
    assert char['combat'] == 'Pressure point'
    assert 'Bending Strength' not in '\n'.join(avatar_messages(char))
    assert engine.rng.calls == len(script)


@pytest.mark.parametrize('mode,value,extra,expected', [
    ('force', 20, [], 20), ('shift', -2, [], 10),
    ('good', 50, [], 16), ('bad', 50, [], 6),
    ('extra_high', 3, [3, 19, 7], 19), ('extra_low', 3, [3, 19, 7], 3)])
def test_all_numeric_rigger_modes_apply_to_strength(mode, value, extra, expected, make_engine):
    script = draws(1, strength=12) + extra
    engine = make_engine(script)
    arm(engine, mode, value)
    outcome = engine.avatar(42, 200)
    char = outcome['character']
    assert char['bending_strength'] == expected
    assert values_of(char['rolls']['bending_strength']) == [expected]
    assert char['rolls']['bending_strength']['expression'] == '1d20'
    assert char['stats'] == [1, 2, 3, 4, 5, 6]
    assert engine.rng.calls == len(script) and active(engine)[0]['remaining'] == 1
    assert outcome['notice'] is None


@pytest.mark.parametrize('mode,value,natural,bender,extras', [
    ('force', 61, 60, True, []), ('force', 60, 61, False, []),
    ('good', 2, 60, True, []), ('bad', 1, 61, False, []),
    ('extra_high', 1, 60, True, [61]), ('extra_low', 1, 61, False, [60])])
def test_final_bending_result_controls_whether_strength_is_drawn(mode, value, natural, bender, extras, make_engine):
    # Fire needs no element d3. After the controlled check comes strength OR combat.
    script = [11, 1, 2, 3, 4, 5, 6, 1, 1, natural] + extras + [17 if bender else 81]
    engine = make_engine(script)
    arm(engine, mode, value, part='bending')
    char = engine.avatar(42, 100)['character']
    assert char['bender'] is bender
    assert char['bending_strength'] == (17 if bender else None)
    assert ('bending_strength' in char['rolls']) is bender
    assert ('combat' in char['rolls']) is (not bender)
    assert engine.rng.calls == len(script)


def test_strength_rigs_stack_in_existing_order_and_spend_one_use_each(make_engine):
    engine = make_engine(draws(26, strength=3) + [14])
    for mode, value in [('extra_high', 1), ('good', 50), ('shift', -2), ('force', 19)]:
        arm(engine, mode, value)
    char = engine.avatar(42, 100)['character']
    assert char['bending_strength'] == 19
    assert [rule['remaining'] for rule in active(engine)] == [1, 1, 1, 1]
    history = engine.control_status(1, is_dm=True)['last']
    assert '3 → 14' in history and '14 → 17' in history
    assert '17 → 15' in history and '15 → 19' in history


def test_strength_specific_luck_overrides_whole_character_luck(make_engine):
    engine = make_engine([1] * 8 + [60, 1, 12])
    arm(engine, 'good', 100, part=None)
    arm(engine, 'bad', 50)
    char = engine.avatar(42, 100)['character']
    assert char['bending'] == 'Earth bending' and char['bending_strength'] == 6
    assert char['stats'] == [20] * 6
    assert [rule['remaining'] for rule in active(engine)] == [1, 1]


def test_all_six_stats_control_does_not_change_separate_strength(make_engine):
    engine = make_engine(draws(1, strength=12))
    arm(engine, 'good', 100, part='stats')
    char = engine.avatar(42, 100)['character']
    assert char['stats'] == [20] * 6 and char['bending_strength'] == 12


@pytest.mark.parametrize('uses,remaining', [(1, []), (2, [1]), ('permanent', [None])])
def test_strength_rig_cannot_create_bending_and_skipped_duration_is_consistent(uses, remaining, make_engine):
    engine = make_engine(draws(76, bending=60, end=81))
    arm(engine, 'extra_high', 100, uses=uses)
    outcome = engine.avatar(42, 100)
    assert not outcome['character']['bender']
    assert 'bending_strength' not in outcome['character']['rolls']
    assert engine.rng.calls == 10  # No strength die or its 100 extra candidates.
    assert [rule['remaining'] for rule in active(engine)] == remaining
    assert bool(outcome['notice']) is (uses != 'permanent')


def test_strength_target_scope_duration_and_receipt_persist_across_servers(tmp_path, make_engine):
    path = tmp_path / 'strength.sqlite3'
    first = make_engine([4] + draws(1) + draws(76), path)
    arm(first, value=18)
    assert first.roll(42, 100)['dice']['total'] == 4
    assert first.avatar(99, 200)['character']['bending_strength'] == 12
    assert active(first)[0]['remaining'] == 2
    result = first.avatar(42, 300, request_id=901)
    assert result['character']['bending_strength'] == 18
    second = make_engine(draws(26), path)
    assert second.avatar(42, 300, request_id=901)['character'] == result['character']
    assert second.rng.calls == 0 and active(second)[0]['remaining'] == 1
    assert second.avatar(42, 400)['character']['bending_strength'] == 18
    assert not active(second)


def test_strength_receipt_failure_rolls_back_consumption(make_engine, monkeypatch):
    engine = make_engine(draws(1) * 2)
    arm(engine, uses=1)
    before = engine.control_status(1, is_dm=True)
    remember = engine.store.remember
    def fail(*args):
        raise sqlite3.OperationalError('simulated write failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        engine.avatar(42, 100, request_id=902)
    assert engine.control_status(1, is_dm=True) == before
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0
    monkeypatch.setattr(engine.store, 'remember', remember)
    assert engine.avatar(42, 100, request_id=902)['character']['bending_strength'] == 18
    assert not active(engine)


@pytest.mark.parametrize('value', [0, 21])
def test_strength_force_rejects_values_outside_d20(value, make_engine):
    engine = make_engine()
    arm(engine)
    before = active(engine)
    with pytest.raises(RollError, match='1 to 20'):
        arm(engine, value=value)
    assert active(engine) == before and engine.rng.calls == 0


def test_private_panel_can_apply_edit_and_track_strength(make_engine):
    async def check():
        engine = make_engine(draws(1))
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        roll = select(panel, 'roll')
        assert len(roll.options) <= 25
        assert any(option.value == 'bending_strength' for option in roll.options)
        roll._values = ['bending_strength']
        await roll.callback(owner_event(1000))
        assert set(panel.allowed_modes()) == {'force', 'shift', 'good', 'bad', 'extra_high', 'extra_low'}
        modal = PanelInput(panel, 'value')
        modal.field._value = '18'
        await modal.on_submit(owner_event(1001))
        panel.draft.update(target='42', uses=2)
        for event in (owner_event(owner=2), owner_event(guild=100)):
            await button(panel, 'Apply').callback(event)
            assert not active(engine)
        await button(panel, 'Apply').callback(owner_event(1002))
        assert engine.avatar(42, 100)['character']['bending_strength'] == 18
        await button(panel, 'Active settings').callback(owner_event(1003))
        payload = panel.embed().to_dict()
        assert 'Bending Strength' in json.dumps(payload) and '1 use left' in json.dumps(payload)
        picked = select(panel, 'active')
        picked._values = [str(active(engine)[0]['id'])]
        await picked.callback(owner_event(1004))
        await button(panel, 'Edit selected').callback(owner_event(1005))
        assert panel.roll == 'bending_strength' and panel.draft['uses'] == 1
        assert len(panel.to_components()) <= 5 and len(panel.embed()) <= 6000
        reopened = RiggerPanel(bot)
        assert reopened.roll == 'bending_strength'
        reopened.stop()
        modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('bender', [True, False])
def test_avatar_callback_shows_only_final_strength_for_benders(bender, make_engine):
    async def check():
        script = draws(1, bending=61 if bender else 60, strength=4) + ([17, 9] if bender else [])
        engine = make_engine(script)
        arm(engine, 'extra_high', 2, target=2, uses='permanent')
        bot = DiceBot(engine)
        bot.notify_creator = AsyncMock()
        event = interaction()
        await bot.tree.get_command('avatar').callback(event)
        public = event.edit_original_response.await_args.kwargs['content']
        if bender:
            assert '**Bending Strength:** `17` (1d20)' in public
        else:
            assert 'Bending Strength' not in public and 'Non-bender' in public
        assert 'extra' not in public.lower() and 'rigger' not in public.lower()
        assert length(public) <= 2000 and engine.rng.calls == len(script)
        bot.notify_creator.assert_not_awaited()
        await bot.close()
    asyncio.run(check())


def test_old_character_receipt_replays_without_inventing_a_strength_roll(make_engine):
    engine = make_engine(draws(1))
    old = engine.avatar(42, 100)
    del old['character']['bending_strength']
    del old['character']['rolls']['bending_strength']
    signature = json.dumps([1, '42', '100', 'avatar-v1'])
    engine.store.remember('avatar', 903, signature, old)
    calls = engine.rng.calls
    arm(engine, value=20)
    replay = engine.avatar(42, 100, request_id=903)
    assert replay['replayed'] and replay['character'] == old['character']
    assert 'Bending Strength' not in '\n'.join(avatar_messages(replay['character']))
    assert engine.rng.calls == calls and active(engine)[0]['remaining'] == 2
