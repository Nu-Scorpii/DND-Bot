"""Durations count commands, preserve complete characters, and remain private."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.controls import MAX_USES, rig_uses
from ua_bot.dice import values_of
from ua_bot.errors import RollError
from ua_bot.rigger_ui import PanelInput, RiggerPanel
from test_avatar import draws
from test_rigger_panel import button, owner_event, select
from test_stacked_rigs import active


@pytest.mark.parametrize('uses,expected', [(1, 1), ('3', 3), (' 12 ', 12), (MAX_USES, MAX_USES),
                                         ('permanent', None), (' Permanent ', None), (None, None)])
def test_duration_accepts_counts_or_permanent(uses, expected):
    assert rig_uses('force', uses) == expected


@pytest.mark.parametrize('uses', [0, -1, MAX_USES + 1, True, False, 1.5, '1.5', 'forever', '', '0', [], {}])
def test_invalid_duration_does_not_change_other_rigs(uses, make_engine):
    engine = make_engine()
    engine.configure(1, is_dm=True, mode='force', value=15, target=42, uses=3)
    before = active(engine)
    with pytest.raises(RollError):
        engine.configure(1, is_dm=True, mode='good', value=50, target=42, uses=uses)
    assert active(engine) == before


@pytest.mark.parametrize('mode,value,expected', [('force', 15, 15), ('shift', 2, 10),
                                              ('good', 50, 14), ('bad', 50, 4)])
@pytest.mark.parametrize('uses', [2, 'permanent'])
def test_each_numeric_mode_can_expire_or_be_permanent_across_servers(mode, value, expected, uses, make_engine):
    engine = make_engine([8] * 3)
    engine.configure(1, is_dm=True, mode=mode, value=value, target=42, uses=uses)
    totals = []
    for index in range(3):
        totals.append(engine.roll(42, 100 + index, request_id=900 + index)['dice']['total'])
        remaining = active(engine)
        if uses == 'permanent':
            assert remaining[0]['remaining'] is None
        elif index == 0:
            assert remaining[0]['remaining'] == 1
        else:
            assert not remaining
    assert totals == ([expected] * 3 if uses == 'permanent' else [expected, expected, 8])
    assert engine.rng.calls == 3


@pytest.mark.parametrize('uses', [2, 'permanent'])
def test_text_faces_also_support_finite_and_permanent_duration(uses, make_engine):
    engine = make_engine(['Sun'] * 3)
    engine.configure(1, is_dm=True, mode='face', value='Heavy Rain', target=42, uses=uses)
    outcomes = [values_of(engine.roll(42, 100, faces=['Sun', 'Heavy Rain'])['dice'])[0] for _ in range(3)]
    assert outcomes == (['Heavy Rain'] * 3 if uses == 'permanent' else ['Heavy Rain', 'Heavy Rain', 'Sun'])
    assert bool(active(engine)) is (uses == 'permanent')


def test_a_large_custom_pool_spends_one_use_and_keeps_bonus(make_engine):
    engine = make_engine([5] * 12)
    engine.configure(1, is_dm=True, mode='shift', value=3, target=42, uses=2)
    result = engine.roll(42, 100, '6d20+2', bonus=4)['dice']
    assert result['total'] == 39 and result['bonus'] == 4
    assert active(engine)[0]['remaining'] == 1
    engine.roll(42, 200, '6d20+2', bonus=4)
    assert not active(engine) and engine.rng.calls == 12


def test_avatar_luck_affects_all_six_dice_before_spending_one_use(make_engine):
    character_draws = [8] + [10] * 6 + [76, 100, 2, 12]
    engine = make_engine(character_draws * 4)
    for part, mode, value, uses in [(None, 'good', 25, 3), ('stats', 'good', 50, 2),
                                   ('stat1', 'force', 4, 1), ('stat2', 'shift', 2, 'permanent')]:
        engine.configure(1, is_dm=True, mode=mode, value=value, target=42, scope='avatar', part=part, uses=uses)
    first = engine.avatar(42, 100)['character']
    assert first['stats'] == [4, 17, 15, 15, 15, 15] and first['iq'] == 11
    assert {r['part']: r['remaining'] for r in active(engine)} == {None: 2, 'stats': 1, 'stat2': None}
    second = engine.avatar(42, 200)['character']
    assert second['stats'] == [15, 17, 15, 15, 15, 15]
    assert {r['part']: r['remaining'] for r in active(engine)} == {None: 1, 'stat2': None}
    third = engine.avatar(42, 300)['character']
    assert third['stats'] == [13, 15, 13, 13, 13, 13]
    fourth = engine.avatar(42, 400)['character']
    assert fourth['stats'] == [10, 12, 10, 10, 10, 10] and fourth['iq'] == 8
    assert len(active(engine)) == 1 and active(engine)[0]['remaining'] is None
    assert engine.rng.calls == 44


def test_all_six_individual_stat_rigs_can_have_different_durations(make_engine):
    engine = make_engine(draws(1) * 7)
    for index in range(1, 7):
        engine.configure(1, is_dm=True, mode='force', value=21 - index, target=42,
                         scope='avatar', part=f'stat{index}', uses=index)
    for use in range(1, 8):
        stats = engine.avatar(42, 100 + use)['character']['stats']
        assert stats == [21 - index if use <= index else index for index in range(1, 7)]
        assert {r['part']: r['remaining'] for r in active(engine)} == {
            f'stat{index}': index - use for index in range(use + 1, 7)}
    assert not active(engine) and engine.rng.calls == 77


def test_targets_scopes_invalid_rolls_and_replays_do_not_spend_uses(make_engine):
    engine = make_engine([8] + draws(1) + [8, 8] + draws(1))
    arguments = dict(is_dm=True, mode='force', value=15, target=42, uses=3, request_id=700)
    engine.configure(1, **arguments)
    engine.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='iq', uses=2)
    engine.roll(99, 100)
    engine.avatar(99, 100)
    before = active(engine)
    with pytest.raises(RollError):
        engine.roll(42, 100, '1d0')
    assert active(engine) == before
    result = engine.roll(42, 100, request_id=900)
    assert [r['remaining'] for r in active(engine)] == [2, 2]
    assert engine.roll(42, 100, request_id=900)['dice'] == result['dice']
    engine.configure(1, **arguments)  # Duplicate Apply cannot replenish the counter.
    assert [r['remaining'] for r in active(engine)] == [2, 2]
    engine.roll(42, 200, request_id=901)
    assert [r['remaining'] for r in active(engine)] == [1, 2]
    character = engine.avatar(42, 100, request_id=902)['character']
    assert character['iq'] == 20 and [r['remaining'] for r in active(engine)] == [1, 1]
    assert engine.avatar(42, 100, request_id=902)['character'] == character
    assert [r['remaining'] for r in active(engine)] == [1, 1] and engine.rng.calls == 25


def test_explicit_reapply_resets_only_that_rigs_duration(make_engine):
    engine = make_engine([8])
    engine.configure(1, is_dm=True, mode='force', value=15, target=42, uses=2)
    engine.configure(1, is_dm=True, mode='good', value=50, target=42, uses=4)
    ids = [r['id'] for r in active(engine)]
    engine.roll(42, 100)
    engine.configure(1, is_dm=True, mode='force', value=18, target=42, uses=5)
    assert [r['id'] for r in active(engine)] == ids
    assert [r['remaining'] for r in active(engine)] == [5, 3]


def test_text_luck_and_fully_shadowed_luck_keep_their_allowance(make_engine):
    engine = make_engine(['Sun'] + draws(1))
    engine.configure(1, is_dm=True, mode='good', value=50, target=42, uses=2)
    engine.roll(42, 100, faces=['Sun', 'Rain'])
    assert active(engine)[0]['remaining'] == 2
    engine.configure(1, is_dm=True, mode='good', value=50, target=42, scope='avatar', part='stats', uses=2)
    for index in range(1, 7):
        engine.configure(1, is_dm=True, mode='bad', value=50, target=42, scope='avatar', part=f'stat{index}', uses=2)
    engine.avatar(42, 100)
    assert next(r for r in active(engine) if r['part'] == 'stats')['remaining'] == 2
    assert all(r['remaining'] == 1 for r in active(engine) if r['part'] and r['part'].startswith('stat') and r['part'] != 'stats')


@pytest.mark.parametrize('uses,expected', [(1, []), (2, [1]), ('permanent', [None])])
def test_impossible_requests_keep_legal_rolls_and_follow_their_duration(uses, expected, make_engine):
    engine = make_engine([4])
    engine.configure(1, is_dm=True, mode='force', value=100, target=42, uses=uses)
    outcome = engine.roll(42, 100, '1d6')
    assert outcome['dice']['total'] == 4 and 'could not apply' in outcome['notice']
    assert [r['remaining'] for r in active(engine)] == expected


@pytest.mark.parametrize('mode,value', [('force', 3), ('good', 50)])
def test_finite_conditional_parts_reserve_character_uses_and_permanent_parts_stay(mode, value, make_engine):
    engine = make_engine(draws(1) * 2)
    engine.configure(1, is_dm=True, mode=mode, value=value, target=42, scope='avatar', part='tribe', uses=2)
    engine.configure(1, is_dm=True, mode='force', value=3, target=42, scope='avatar', part='element', uses='permanent')
    first = engine.avatar(42, 100)
    assert 'tribe' not in first['character']['rolls'] and 'element' not in first['character']['rolls']
    assert 'one character use' in first['notice']
    assert {r['part']: r['remaining'] for r in active(engine)} == {'tribe': 1, 'element': None}
    engine.avatar(42, 200)
    assert len(active(engine)) == 1 and active(engine)[0]['part'] == 'element'
    assert engine.rng.calls == 22


def test_counts_permanent_rigs_and_receipts_survive_restart(tmp_path, make_engine):
    path = tmp_path / 'durations.sqlite3'
    first = make_engine(draws(1), path)
    first.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='iq', uses=3)
    first.configure(1, is_dm=True, mode='shift', value=2, target=42, scope='avatar', part='stat6', uses='permanent')
    char = first.avatar(42, 100, request_id=900)['character']
    second = make_engine(draws(1), path)
    assert [r['remaining'] for r in active(second)] == [2, None]
    assert second.avatar(42, 100, request_id=900)['character'] == char
    assert second.rng.calls == 0 and [r['remaining'] for r in active(second)] == [2, None]
    second.avatar(42, 200)
    assert [r['remaining'] for r in active(second)] == [1, None]
    third = make_engine(path=path)
    assert [r['remaining'] for r in active(third)] == [1, None]


def test_old_stacked_state_keeps_once_and_permanent_defaults(make_engine):
    engine = make_engine()
    old = {'rules': [dict(id=1, mode='force', value=20, target='42', scope='avatar', part='iq'),
                     dict(id=2, mode='good', value=37, target='42', scope='avatar', part='stats')],
           'next_id': 3, 'last': 'old roll'}
    engine.store.save_controls(1, old)
    assert [r['remaining'] for r in active(engine)] == [1, None]
    assert engine.control_status(1, is_dm=True)['last'] == 'old roll'
    engine.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='stat1', uses=3)
    assert [r['remaining'] for r in active(engine)] == [1, None, 3]


@pytest.mark.parametrize('remaining', [0, -1, True, 'permanent', MAX_USES + 1])
def test_a_corrupt_duration_cannot_disable_unrelated_rigs(remaining, make_engine):
    engine = make_engine()
    engine.configure(1, is_dm=True, mode='force', value=15, target=42, uses=2)
    engine.configure(1, is_dm=True, mode='good', value=37, target=99, uses='permanent')
    state = engine.control_status(1, is_dm=True)
    state['rules'][0]['remaining'] = remaining
    engine.store.save_controls(1, state)
    assert len(active(engine)) == 1 and active(engine)[0]['target'] == '99'


@pytest.mark.parametrize('command', ['roll', 'avatar'])
def test_receipt_write_failure_rolls_back_counters_and_expiration(command, make_engine, monkeypatch):
    engine = make_engine([8] if command == 'roll' else draws(1))
    for mode, value, uses in [('force', 20, 1), ('shift', 2, 3), ('good', 50, 'permanent')]:
        engine.configure(1, is_dm=True, mode=mode, value=value, target=42, scope=command,
                         part='iq' if command == 'avatar' else None, uses=uses)
    before = active(engine)
    def fail(*args):
        raise sqlite3.OperationalError('simulated write failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        getattr(engine, command)(42, 100, request_id=900)
    assert active(engine) == before
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0


def test_duration_form_draft_apply_edit_and_refresh_show_live_remaining_uses(make_engine):
    async def check():
        engine = make_engine([8])
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        await button(panel, 'Duration').callback(owner_event(1000))
        # Duration can be chosen before entering a force value.
        modal = PanelInput(panel, 'duration')
        assert modal.field.default == '1'
        modal.field._value = '3'
        await modal.on_submit(owner_event(1001))
        assert panel.draft['uses'] == 3 and not active(engine)
        panel.draft['value'] = 15
        await button(panel, 'Apply').callback(owner_event(1002))
        engine.roll(1, 100)
        await button(panel, 'Active settings').callback(owner_event(1003))
        assert '2 uses left' in str(panel.embed().to_dict())
        choice = select(panel, 'active')
        choice._values = [str(active(engine)[0]['id'])]
        await choice.callback(owner_event(1004))
        await button(panel, 'Edit selected').callback(owner_event(1005))
        assert panel.draft['uses'] == 2
        permanent = PanelInput(panel, 'duration')
        permanent.field._value = 'permanent'
        await permanent.on_submit(owner_event(1006))
        assert active(engine)[0]['remaining'] == 2  # Still only a draft.
        await button(panel, 'Apply').callback(owner_event(1007))
        assert active(engine)[0]['remaining'] is None
        await panel.refresh_message()
        assert any(field.name == 'Duration' and field.value == 'Permanent' for field in panel.embed().fields)
        payload = panel.to_components()
        assert len(payload) == 4 and all(len(row['components']) <= 5 for row in payload)
        assert len(panel.embed()) <= 6000
        form = permanent.to_dict()
        assert form['components'][0]['component']['type'] == 4
        modal.stop()
        permanent.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('case', ['nonowner', 'server', 'expired', 'changed_roll', 'invalid'])
def test_duration_forms_cannot_bypass_owner_or_stale_draft_guards(case, make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        modal = PanelInput(panel, 'duration')
        modal.field._value = '5'
        event = owner_event()
        if case == 'nonowner':
            event.user.id = 2
        elif case == 'server':
            event.guild_id = 100
        elif case == 'expired':
            panel.stop()
        elif case == 'changed_roll':
            panel.roll = 'iq'
        else:
            modal.field._value = '0'
        before = dict(panel.draft)
        await modal.on_submit(event)
        assert panel.draft == before and not active(engine)
        assert event.response.send_message.await_args.kwargs['ephemeral']
        modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())
