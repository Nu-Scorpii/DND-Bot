"""Hidden extra draws keep legal faces, ordinary public output, and rig semantics."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from conftest import ScriptedRNG
from test_avatar import draws
from test_discord import interaction
from test_rigger_panel import button, owner_event, select
from ua_bot.client import DiceBot
from ua_bot.controls import EXTRA_MODES, MAX_EXTRA_DICE, panel_setting
from ua_bot.dice import prepare, values_of
from ua_bot.display import roll_messages
from ua_bot.errors import RollError
from ua_bot.luck import extra_dice_result
from ua_bot.rigger_ui import PanelInput, RiggerPanel


def active(engine):
    return engine.control_status(1, is_dm=True)['rules']


def arm(engine, mode='extra_high', extra=4, **kwargs):
    return engine.configure(1, is_dm=True, mode=mode, value=extra, target=42, **kwargs)


@pytest.mark.parametrize('mode,expected', [('extra_high', 18), ('extra_low', 2)])
def test_requested_example_draws_five_candidates_and_returns_only_one(mode, expected, make_engine):
    engine = make_engine([3, 18, 2, 7, 10])
    arm(engine, mode, uses=1)
    outcome = engine.roll(42, 100, '1d20+2', request_id=800)
    result = outcome['dice']
    assert engine.rng.calls == 5
    assert result['expression'] == '1d20+2'
    assert values_of(result) == [expected] and result['total'] == expected + 2
    public = '\n'.join(roll_messages(result, 'Hero'))
    assert f'Rolls: `{expected}`' in public and 'Bonus: `+2`' in public
    assert all(word not in public.lower() for word in ('extra', 'rigger', 'candidate', '5d20'))
    assert not outcome['notice'] and not active(engine)
    stored = json.loads(engine.store.db.execute('SELECT result FROM dice_receipts_v3 WHERE kind="roll"').fetchone()[0])
    assert stored == outcome
    assert set(result) == {'expression', 'groups', 'bonus', 'keep', 'total'}


@pytest.mark.parametrize('sides', [1, 3, 6, 17, 20, 100, 1_000_000_000])
@pytest.mark.parametrize('mode', EXTRA_MODES)
def test_any_supported_die_size_uses_that_same_die(sides, mode, make_engine):
    engine = make_engine([1, sides, 1])
    arm(engine, mode, extra=2)
    result = engine.roll(42, 100, f'1d{sides}')['dice']
    assert values_of(result) == [sides if mode == 'extra_high' else 1]
    assert engine.rng.calls == 3


@pytest.mark.parametrize('mode,expected', [('extra_high', [8, 17]), ('extra_low', [2, 4])])
def test_multiple_dice_keep_one_independent_winner_each(mode, expected, make_engine):
    engine = make_engine([3, 4, 8, 2, 6, 17])
    arm(engine, mode, extra=2, uses=2)
    result = engine.roll(42, 100, '2d20-3')['dice']
    assert values_of(result) == expected and result['total'] == sum(expected) - 3
    assert engine.rng.calls == 6 and active(engine)[0]['remaining'] == 1


@pytest.mark.parametrize('mode,expected,total', [('extra_high', [6, 90, 3], 95), ('extra_low', [2, 1, 1], 4)])
def test_mixed_sizes_compare_faces_before_signed_arithmetic(mode, expected, total, make_engine):
    engine = make_engine([3, 40, 2, 6, 2, 90, 1, 3, 1])
    arm(engine, mode, extra=2)
    result = engine.roll(42, 100, '1d6+1d100-1d3+2')['dice']
    assert values_of(result) == expected and result['total'] == total
    assert engine.rng.calls == 9


@pytest.mark.parametrize('mode,expected', [('extra_high', 5), ('extra_low', -10)])
def test_numeric_custom_faces_keep_their_weights_and_real_numeric_order(mode, expected, make_engine):
    engine = make_engine([0, -10, 5, 0])
    arm(engine, mode, extra=3)
    result = engine.roll(42, 100, '1d[0,5,-10,0]+2')['dice']
    assert values_of(result) == [expected] and result['total'] == expected + 2
    assert engine.rng.domains == [(0, 5, -10, 0)] * 4


@pytest.mark.parametrize('mode,expected', [('extra_high', 'Beginner'), ('extra_low', 'Master')])
def test_text_faces_follow_first_occurrence_order_not_alphabetical_order(mode, expected, make_engine):
    engine = make_engine(['Master', 'Beginner', 'Master'])
    arm(engine, mode, extra=2, uses=1)
    result = engine.roll(42, 100, '1d["Master","Beginner","Master"]')['dice']
    assert values_of(result) == [expected] and result['total'] is None
    assert engine.rng.domains == [('Master', 'Beginner', 'Master')] * 3
    assert not active(engine)


def test_mixed_text_numeric_faces_distinguish_types_and_numeric_pool_stays_numeric(make_engine):
    engine = make_engine([5, 2, 'Rain', '5', 20, 1])
    arm(engine, extra=2)
    result = engine.roll(42, 100, '1d[5,"Rain","5",5]+1d20')['dice']
    assert values_of(result) == ['5', 20] and result['total'] is None


@pytest.mark.parametrize('mode,keep,expected', [('extra_high', 'kh1', 12), ('extra_high', 'kl1', 10),
                                              ('extra_low', 'kh1', 6), ('extra_low', 'kl1', 4)])
def test_existing_keep_selection_and_bonus_apply_after_hidden_candidates(mode, keep, expected):
    plan = prepare(f'3d20{keep}+2')
    rng = ScriptedRNG([10, 3, 9])
    result = extra_dice_result(plan, plan.result([2, 8, 4]), mode, 1, rng)
    assert result['total'] == expected and len(values_of(result)) == 3
    assert rng.calls == 3


def test_original_result_remains_a_candidate(make_engine):
    engine = make_engine([20, 1, 2, 3, 4])
    arm(engine)
    assert engine.roll(42, 100)['dice']['total'] == 20


@pytest.mark.parametrize('value', [None, 0, -1, MAX_EXTRA_DICE + 1, True, 1.5, '4.0', '4%', 'four', ''])
def test_invalid_extra_counts_preserve_saved_rigs(value, make_engine):
    engine = make_engine()
    arm(engine, extra=2)
    before = active(engine)
    with pytest.raises(RollError):
        arm(engine, extra=value)
    assert active(engine) == before and engine.rng.calls == 0


def test_maximum_extra_count_is_bounded_and_supports_largest_public_pool(make_engine):
    engine = make_engine([1] * (100 * (MAX_EXTRA_DICE + 1)))
    arm(engine, extra=MAX_EXTRA_DICE)
    result = engine.roll(42, 100, '100d1')['dice']
    assert values_of(result) == [1] * 100 and result['total'] == 100
    assert engine.rng.calls == 100 * (MAX_EXTRA_DICE + 1)


def test_extra_selection_stacks_before_luck_shift_and_exact_force(make_engine):
    engine = make_engine([2, 10])
    arm(engine, extra=1, uses=2)
    engine.configure(1, is_dm=True, mode='good', value=50, target=42, uses=2)
    engine.configure(1, is_dm=True, mode='shift', value=2, target=42, uses=2)
    engine.configure(1, is_dm=True, mode='force', value=18, target=42, uses=2)
    assert engine.roll(42, 100)['dice']['total'] == 18
    history = engine.control_status(1, is_dm=True)['last']
    assert '2 → 10' in history and '10 → 15' in history and '15 → 17' in history and '17 → 18' in history
    assert len(active(engine)) == 4 and all(rule['remaining'] == 1 for rule in active(engine))
    assert engine.rng.calls == 2


def test_highest_and_lowest_share_a_slot_independent_of_percentage_luck(make_engine):
    engine = make_engine()
    arm(engine, extra=2)
    identity = active(engine)[0]['id']
    engine.configure(1, is_dm=True, mode='good', value=50, target=42)
    arm(engine, mode='extra_low', extra=9)
    rules = active(engine)
    assert len(rules) == 2
    assert rules[0]['id'] == identity and rules[0]['mode'] == 'extra_low' and rules[0]['value'] == 9
    assert rules[1]['mode'] == 'good'


def test_other_players_scopes_and_invalid_rolls_do_not_draw_extras_or_spend_uses(make_engine):
    engine = make_engine([7] + draws(76))
    arm(engine, extra=1, uses=1)
    assert engine.roll(99, 200)['dice']['total'] == 7
    engine.avatar(42, 300)
    with pytest.raises(RollError):
        engine.roll(42, 400, '0d20')
    assert engine.rng.calls == 12 and active(engine)[0]['remaining'] == 1


def test_replay_persistence_and_finite_duration_across_servers(tmp_path, make_engine):
    path = tmp_path / 'extra.sqlite3'
    engine = make_engine([2, 18, 3], path=path)
    arm(engine, extra=2, uses=2)
    first = engine.roll(42, 100, '1d20', request_id=800)
    engine.store.close()
    resumed = make_engine([7, 1, 20], path=path)
    assert active(resumed)[0]['value'] == 2 and active(resumed)[0]['remaining'] == 1
    assert resumed.roll(42, 100, '1d20', request_id=800)['dice'] == first['dice']
    assert resumed.rng.calls == 0 and active(resumed)[0]['remaining'] == 1
    assert resumed.roll(42, 200, '1d20', request_id=801)['dice']['total'] == 20
    assert resumed.rng.calls == 3 and not active(resumed)


def test_failed_receipt_rolls_back_rig_consumption(make_engine, monkeypatch):
    engine = make_engine([2, 18])
    arm(engine, extra=1, uses=1)
    before = engine.control_status(1, is_dm=True)
    def fail(*args):
        raise RuntimeError('simulated storage failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(RuntimeError):
        engine.roll(42, 100, request_id=800)
    assert engine.control_status(1, is_dm=True) == before
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0


def test_whole_avatar_uses_final_winners_for_conditional_branches(make_engine):
    engine = make_engine([8, 12] + [1, 2, 3, 4, 5, 6] + [10, 11, 12, 13, 14, 15] +
                         [70, 90, 60, 61, 1, 3, 4, 18])
    arm(engine, extra=1, scope='avatar', uses=2)
    character = engine.avatar(42, 100)['character']
    assert character['iq'] == 12 and character['stats'] == [10, 11, 12, 13, 14, 15]
    assert character['nation'] == 'Nomad' and character['bending'] == 'Earth bending'
    assert set(character['rolls']) == {'iq', 'stats', 'nation', 'bending', 'element', 'bending_strength'}
    assert character['bending_strength'] == 18
    assert engine.rng.calls == 22 and active(engine)[0]['remaining'] == 1


def test_all_six_stats_roll_extras_only_once_per_die(make_engine):
    engine = make_engine([11, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 76, 61, 3, 12])
    arm(engine, extra=1, scope='avatar', part='stats', uses=2)
    character = engine.avatar(42, 100)['character']
    assert character['iq'] == 11 and character['stats'] == [7, 8, 9, 10, 11, 12]
    assert engine.rng.calls == 17 and active(engine)[0]['remaining'] == 1


def test_individual_stat_extra_setting_overrides_all_stats_only_for_its_slot(make_engine):
    engine = make_engine([11, 1, 2, 3, 4, 5, 6, 11, 20, 1, 13, 14, 15, 16, 76, 61, 3, 12])
    arm(engine, extra=1, scope='avatar', part='stats', uses=2)
    arm(engine, mode='extra_low', extra=2, scope='avatar', part='stat2', uses=2)
    character = engine.avatar(42, 100)['character']
    assert character['stats'] == [11, 1, 13, 14, 15, 16]
    assert engine.rng.calls == 18 and all(rule['remaining'] == 1 for rule in active(engine))


def test_fully_overridden_stats_setting_does_not_draw_or_spend_uses(make_engine):
    engine = make_engine([11, 1, 2, 3, 4, 5, 6] + [20] * 6 + [76, 61, 3, 12])
    arm(engine, extra=100, scope='avatar', part='stats', uses=2)
    for index in range(1, 7):
        arm(engine, mode='extra_low', extra=1, scope='avatar', part=f'stat{index}', uses=1)
    assert engine.avatar(42, 100)['character']['stats'] == [1, 2, 3, 4, 5, 6]
    assert engine.rng.calls == 17
    assert len(active(engine)) == 1 and active(engine)[0]['remaining'] == 2


def test_lowest_bending_at_sixty_uses_combat_instead_of_nomad_element(make_engine):
    engine = make_engine([11, 1, 2, 3, 4, 5, 6, 76, 61, 60, 81])
    arm(engine, mode='extra_low', extra=1, scope='avatar', part='bending')
    character = engine.avatar(42, 100)['character']
    assert not character['bender'] and character['combat'] == 'Pressure point'
    assert 'element' not in character['rolls'] and engine.rng.calls == 11
    assert active(engine)[0]['remaining'] is None


def test_skipped_finite_conditional_rig_spends_one_character_use_without_extra_draws(make_engine):
    engine = make_engine(draws(76))
    arm(engine, extra=4, scope='avatar', part='tribe', uses=1)
    outcome = engine.avatar(42, 100)
    assert engine.rng.calls == 11 and 'tribe was not rolled' in outcome['notice']
    assert not active(engine)


def test_corrupt_extra_count_drops_only_that_entry(make_engine):
    engine = make_engine()
    arm(engine, extra=2)
    engine.configure(1, is_dm=True, mode='good', value=50, target=42)
    state = engine.control_status(1, is_dm=True)
    state['rules'][0]['value'] = MAX_EXTRA_DICE + 1
    engine.store.save_controls(1, state)
    assert len(active(engine)) == 1 and active(engine)[0]['mode'] == 'good'


def test_owner_ui_sets_count_direction_duration_and_edits_existing_stats_rig(make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        choice = select(panel, 'mode')
        choice._values = ['extra_high']
        await choice.callback(owner_event(1000))
        assert panel.draft['value'] == 4 and not active(engine)
        assert '5 total each' in panel.embed().fields[2].value
        assert panel.embed().fields[3].value == 'Permanent'
        await button(panel, 'Extra dice').callback(owner_event(1001))
        modal = PanelInput(panel, 'value')
        assert modal.title == 'Extra dice' and modal.field.default == '4'
        modal.field._value = '9'
        await modal.on_submit(owner_event(1002))
        assert panel.draft['value'] == 9 and not active(engine)
        choice = select(panel, 'mode')
        choice._values = ['extra_low']
        await choice.callback(owner_event(1003))
        assert panel.draft['value'] == 9
        scope = select(panel, 'roll')
        scope._values = ['stats']
        await scope.callback(owner_event(1004))
        preset = select(panel, 'outcome')
        preset._values = ['4']
        await preset.callback(owner_event(1005))
        duration = PanelInput(panel, 'duration')
        duration.field._value = '3'
        await duration.on_submit(owner_event(1006))
        await button(panel, 'Apply').callback(owner_event(1007))
        rule = active(engine)[0]
        assert (rule['part'], rule['mode'], rule['value'], rule['remaining']) == ('stats', 'extra_low', 4, 3)
        await button(panel, 'Active settings').callback(owner_event(1008))
        chosen = select(panel, 'active')
        chosen._values = [str(rule['id'])]
        await chosen.callback(owner_event(1009))
        await button(panel, 'Edit selected').callback(owner_event(1010))
        assert panel.page == 'edit' and panel.draft['mode'] == 'extra_low' and panel.draft['uses'] == 3
        reopened = RiggerPanel(bot)
        assert reopened.roll == 'stats' and reopened.draft['mode'] == 'extra_low'
        reopened.stop()
        modal.stop()
        duration.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('owner,guild', [(2, None), (1, 100)])
def test_extra_count_form_and_engine_still_reject_nonowner_or_server_access(owner, guild, make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.draft.update(mode='extra_high', value=4)
        panel.build_items()
        modal = PanelInput(panel, 'value')
        modal.field._value = '100'
        await modal.on_submit(owner_event(owner=owner, guild=guild))
        await button(panel, 'Apply').callback(owner_event(owner=owner, guild=guild))
        assert panel.draft['value'] == 4 and not active(engine)
        with pytest.raises(RollError):
            engine.configure(owner, is_dm=guild is None, mode='extra_high', value=4, target=42)
        modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_public_callback_reveals_only_selected_result_and_declared_penalty(make_engine):
    async def check():
        engine = make_engine([3, 17, 6, 2, 11])
        engine.configure(1, is_dm=True, mode='extra_high', value=4, target=2, uses=1)
        bot = DiceBot(engine)
        bot.notify_creator = AsyncMock()
        event = interaction()
        await bot.tree.get_command('roll').callback(event, dice='1d20-2')
        public = event.edit_original_response.await_args.kwargs['content']
        assert 'Rolls: `17`' in public and 'Penalty: `-2`' in public and '**Total: 15**' in public
        assert all(f'`{value}`' not in public for value in (3, 6, 2, 11))
        assert 'extra' not in public.lower() and 'rigger' not in public.lower()
        assert engine.rng.calls == 5 and not active(engine)
        bot.notify_creator.assert_not_awaited()
        await bot.close()
    asyncio.run(check())


def test_extra_controls_payloads_and_private_status_remain_valid(make_engine):
    async def check():
        engine = make_engine()
        arm(engine, extra=4)
        assert 'keep highest' in engine.private_message(1, '!Rigger status', is_dm=True)
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        for mode in EXTRA_MODES:
            for roll in ('roll', 'avatar', 'stats', 'stat1', 'bending'):
                panel.roll = roll
                panel.draft.update(mode=mode, value=MAX_EXTRA_DICE)
                panel.build_items()
                assert len(panel.to_components()) <= 5 and len(panel.embed()) <= 6000
                assert len(select(panel, 'mode').options) <= 25
                assert all(len(option.label) <= 100 and len(option.description or '') <= 100
                           for choice in panel.children if isinstance(choice, discord.ui.Select) for option in choice.options)
        panel.stop()
        await bot.close()
    asyncio.run(check())
