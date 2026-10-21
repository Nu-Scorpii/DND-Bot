import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.controls import panel_setting
from ua_bot.dice import prepare, values_of
from ua_bot.errors import RollError
from ua_bot.luck import luck_result
from ua_bot.rigger_ui import PanelInput, RiggerPanel, PAGE_SIZE, ROLLS
from test_avatar import draws
from test_rigger_panel import button, owner_event, select


def arm(engine, part, value, mode='force', target=42, request=None):
    return engine.configure(1, is_dm=True, mode=mode, value=value, target=target,
                            scope='avatar', part=part, request_id=request)


def active(engine):
    return engine.control_status(1, is_dm=True)['rules']


@pytest.mark.parametrize('mode,percent,expected', [
    ('good', 1, 8), ('good', 25, 11), ('good', 50, 14), ('good', 75, 17), ('good', 100, 20),
    ('bad', 1, 8), ('bad', 25, 6), ('bad', 50, 4), ('bad', 75, 3), ('bad', 100, 1)])
def test_luck_percent_is_strength_not_a_random_activation_chance(mode, percent, expected, make_engine):
    engine = make_engine([8, 8])
    engine.configure(1, is_dm=True, mode=mode, value=percent, target=42)
    for server in (100, 200):
        assert engine.roll(42, server)['dice']['total'] == expected
    assert engine.rng.calls == 2
    assert active(engine)[0]['value'] == percent


@pytest.mark.parametrize('value', [0, 101, -1, 1.5, True, '1.5', 'one', '', '50%%'])
def test_invalid_strength_preserves_other_active_rigs(value, make_engine):
    engine = make_engine()
    arm(engine, 'nation', 76)
    before = active(engine)
    with pytest.raises(RollError):
        arm(engine, 'iq', value, mode='good')
    assert active(engine) == before


@pytest.mark.parametrize('value,expected', [(1, 1), (100, 100), ('50%', 50), (' 75% ', 75)])
def test_percent_input_is_easy_to_enter(value, expected):
    assert panel_setting('bad', value)['value'] == expected


@pytest.mark.parametrize('mode,faces,expected', [('good', [3, 16], [12, 8]), ('bad', [3, 16], [2, 18])])
def test_percentage_respects_subtraction_and_leaves_bonus_unchanged(mode, faces, expected):
    plan = prepare('1d20-1d20+2', bonus=3)
    result = luck_result(plan, plan.result(faces), mode, 50)
    assert values_of(result) == expected
    assert result['bonus'] == 3 and result == plan.result(expected)


@pytest.mark.parametrize('mode,keep,expected', [('good', 'high', 15), ('bad', 'high', 5),
                                              ('good', 'low', 11), ('bad', 'low', 1)])
def test_strength_respects_kept_dice(mode, keep, expected):
    plan = prepare('2d20', keep=keep)
    assert luck_result(plan, plan.result([2, 10]), mode, 50)['total'] == expected


def test_sparse_weighted_numeric_faces_stay_legal_and_strength_is_monotonic():
    plan = prepare(faces=[-10, -10, 0, 30])
    for mode in ('good', 'bad'):
        totals = [luck_result(plan, plan.result([0]), mode, strength)['total'] for strength in range(1, 101)]
        assert all(value in (-10, 0, 30) for value in totals)
        assert totals == sorted(totals, reverse=mode == 'bad')
        assert totals[-1] == (30 if mode == 'good' else -10)


def test_stacking_nation_bending_and_all_six_individual_stats_changes_one_avatar(make_engine):
    engine = make_engine([8] + [10] * 6 + [1, 60, 1, 12])
    arm(engine, 'iq', 19)
    arm(engine, 'nation', 76)
    arm(engine, 'bending', 61)
    arm(engine, 'element', 2)
    expected = [1, 20, 7, 12, 17, 4]
    for index, value in enumerate(expected, 1):
        arm(engine, f'stat{index}', value)
    assert len(active(engine)) == 10
    result = engine.avatar(42, 200)
    char = result['character']
    assert char['iq'] == 19 and char['stats'] == expected
    assert char['nation'] == 'Nomad' and char['bending'] == 'Water bending'
    assert set(char['rolls']) == {'iq', 'stats', 'nation', 'bending', 'element', 'bending_strength'}
    assert char['rolls']['stats'] == prepare('6d20').result(expected)
    assert engine.rng.calls == 11 and not active(engine) and result['notice'] is None


def test_avatar_rigs_ignore_custom_rolls_and_other_players(make_engine):
    engine = make_engine([5] + draws(1) + draws(26))
    arm(engine, 'iq', 20)
    arm(engine, 'bending', 100)
    arm(engine, 'nation', 26)
    before = active(engine)
    assert engine.roll(42, 100, '1d20')['dice']['total'] == 5
    engine.avatar(99, 200)
    assert active(engine) == before
    char = engine.avatar(42, 300)['character']
    assert char['iq'] == 20 and char['bender'] and char['nation'] == 'Water Tribes'
    assert not active(engine)


def test_six_stat_rigs_can_mix_force_shift_and_percent_luck(make_engine):
    engine = make_engine([8] + [10] * 6 + [1, 1, 61, 12])
    arm(engine, 'stat1', 18)
    arm(engine, 'stat2', 3, 'shift')
    arm(engine, 'stat3', 50, 'good')
    arm(engine, 'stat4', 50, 'bad')
    arm(engine, 'stat5', -3, 'shift')
    arm(engine, 'stat6', 1)
    assert engine.avatar(42, 100)['character']['stats'] == [18, 13, 15, 5, 7, 1]
    assert {(r['part'], r['mode']) for r in active(engine)} == {('stat3', 'good'), ('stat4', 'bad')}


def test_specific_luck_overrides_broader_luck_and_force_wins_last(make_engine):
    engine = make_engine([8] + [10] * 6 + [1, 1, 61, 12])
    arm(engine, None, 25, 'good')
    arm(engine, 'stats', 50, 'good')
    arm(engine, 'stat2', 100, 'bad')
    arm(engine, 'stat3', 20, 'force')
    arm(engine, 'stat3', -2, 'shift')
    # Nation/family/bending/element are also affected by whole-character luck.
    engine.rng.values = iter([8] + [10] * 6 + [1, 1, 61, 12])
    char = engine.avatar(42, 100)['character']
    assert char['iq'] == 11 and char['stats'] == [15, 1, 20, 15, 15, 15]
    assert len(active(engine)) == 3


def test_apply_updates_only_same_player_roll_and_kind(make_engine):
    engine = make_engine()
    arm(engine, 'iq', 25, 'good')
    first_id = active(engine)[0]['id']
    arm(engine, 'iq', 18)
    arm(engine, 'iq', 2, 'shift')
    arm(engine, 'nation', 76)
    arm(engine, 'iq', 100, 'good', target=99)
    arm(engine, 'iq', 75, 'bad')
    assert len(active(engine)) == 5
    changed = next(r for r in active(engine) if r['id'] == first_id)
    assert changed['mode'] == 'bad' and changed['value'] == 75


@pytest.mark.parametrize('part', [f'stat{index}' for index in range(1, 7)])
@pytest.mark.parametrize('value', [0, 21])
def test_each_stat_force_range_is_one_to_twenty(part, value, make_engine):
    engine = make_engine()
    with pytest.raises(RollError, match='1 to 20'):
        arm(engine, part, value)
    assert not active(engine)


@pytest.mark.parametrize('mode,strength,natural,bender', [('good', 1, 60, False), ('good', 2, 60, True), ('bad', 1, 61, False)])
def test_percent_bending_drives_nomad_element_branch(mode, strength, natural, bender, make_engine):
    scripted = draws(76, bending=61 if bender else 60, end=2)
    scripted[8] = natural
    engine = make_engine(scripted)
    arm(engine, 'bending', strength, mode)
    char = engine.avatar(42, 100)['character']
    assert char['bender'] is bender
    assert ('element' in char['rolls']) is bender
    assert ('combat' in char['rolls']) is (not bender)
    assert engine.rng.calls == 10 + int(bender)


def test_skipped_and_impossible_rigs_clear_individually_without_erasing_luck_or_other_players(make_engine):
    engine = make_engine(draws(76, bending=60, end=1))
    arm(engine, 'tribe', 3)
    arm(engine, 'stat1', 100, 'shift')
    arm(engine, 'stat2', 20)
    arm(engine, 'iq', 10, 'good')
    arm(engine, 'iq', 5, target=99)
    result = engine.avatar(42, 100)
    assert result['character']['stats'][:2] == [1, 20]
    assert 'could not apply' in result['notice'] and 'tribe was not rolled' in result['notice']
    assert {(r['target'], r['mode']) for r in active(engine)} == {('42', 'good'), ('99', 'force')}


def test_multi_rig_receipts_and_consumption_survive_restart(tmp_path, make_engine):
    path = tmp_path / 'stacked.sqlite3'
    first = make_engine(path=path)
    arm(first, 'iq', 20, request=700)
    arm(first, 'stat6', 20, request=701)
    arm(first, 'bending', 30, 'bad', request=702)
    second = make_engine(draws(1, bending=100), path)
    char = second.avatar(42, 100, request_id=900)['character']
    assert char['iq'] == 20 and char['stats'][-1] == 20 and char['bender']
    arm(second, 'nation', 76, request=703)
    third = make_engine(path=path)
    assert third.avatar(42, 100, request_id=900)['character'] == char
    arm(third, 'iq', 20, request=700)
    assert {(r['part'], r['mode']) for r in active(third)} == {('bending', 'bad'), ('nation', 'force')}
    assert third.rng.calls == 0


def test_v32_single_rig_migrates_and_can_stack_without_losing_last_status(tmp_path, make_engine):
    path = tmp_path / 'migration.sqlite3'
    old = {'mode': 'good', 'value': None, 'target': '42', 'scope': 'avatar', 'part': 'iq', 'last': 'old roll'}
    engine = make_engine(path=path)
    engine.store.save_setting(1, old)
    arm(engine, 'stat1', 20)
    status = engine.control_status(1, is_dm=True)
    assert len(status['rules']) == 2 and status['rules'][0]['value'] == 100
    assert status['last'] == 'old roll'
    again = make_engine(path=path)
    assert active(again) == active(engine)


def test_remove_one_clear_player_and_clear_all_are_independent_and_ids_are_not_reused(make_engine):
    engine = make_engine()
    arm(engine, 'iq', 20)
    arm(engine, 'stat1', 20)
    arm(engine, 'iq', 20, target=99)
    removed_id = active(engine)[0]['id']
    engine.remove_control(1, is_dm=True, rule_id=removed_id, request_id=900)
    arm(engine, 'iq', 10)
    assert active(engine)[-1]['id'] > removed_id
    engine.remove_control(1, is_dm=True, rule_id=removed_id, request_id=900)
    assert len(active(engine)) == 3
    engine.remove_control(1, is_dm=True, target=42)
    assert len(active(engine)) == 1 and active(engine)[0]['target'] == '99'
    engine.configure(1, is_dm=True, mode='off')
    assert not active(engine)


@pytest.mark.parametrize('owner,is_dm', [(2, True), (1, False)])
def test_active_status_and_removal_keep_owner_dm_guards(owner, is_dm, make_engine):
    engine = make_engine()
    arm(engine, 'iq', 20)
    with pytest.raises(RollError, match='private'):
        engine.remove_control(owner, is_dm=is_dm, target=42)
    with pytest.raises(RollError, match='private'):
        engine.control_status(owner, is_dm=is_dm)
    assert len(active(engine)) == 1


def test_failed_avatar_write_rolls_back_all_rig_consumption(make_engine, monkeypatch):
    engine = make_engine(draws(1))
    arm(engine, 'iq', 20)
    arm(engine, 'stat1', 20)
    before = active(engine)
    def fail(*args):
        raise sqlite3.OperationalError('simulated write failure')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        engine.avatar(42, 100, request_id=900)
    assert active(engine) == before
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 0


def test_one_corrupt_stored_rig_does_not_erase_other_rigs(make_engine):
    engine = make_engine()
    arm(engine, 'iq', 20)
    arm(engine, 'stat1', 20)
    state = engine.store.controls(1)
    state['rules'][0]['value'] = 'broken'
    engine.store.save_controls(1, state)
    assert len(active(engine)) == 1 and active(engine)[0]['part'] == 'stat1'


def test_luck_modal_and_quick_percent_are_drafts_until_apply(make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        roll = select(panel, 'roll')
        roll._values = ['stat1']
        await roll.callback(owner_event(1000))
        mode = select(panel, 'mode')
        mode._values = ['good']
        await mode.callback(owner_event(1001))
        assert panel.draft['value'] == 50
        modal = PanelInput(panel, 'value')
        modal.field._value = '37%'
        await modal.on_submit(owner_event(1002))
        assert panel.draft['value'] == 37 and not active(engine)
        await button(panel, 'Apply').callback(owner_event(1003))
        assert active(engine)[0]['value'] == 37
        assert any(field.name == 'Change' and field.value == 'Good luck 37%' for field in panel.embed().fields)
        modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_active_manager_paginates_filters_edits_and_removes_one_rig(make_engine):
    async def check():
        engine = make_engine()
        for player in (42, 99):
            for part in ('iq', 'stat1', 'stat2', 'stat3', 'stat4', 'stat5'):
                arm(engine, part, 20, target=player)
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.draft['target'] = '42'
        await button(panel, 'Active settings').callback(owner_event(1000))
        assert panel.page == 'active' and len(select(panel, 'active').options) == PAGE_SIZE
        assert '12' in panel.embed().title
        await button(panel, 'Next').callback(owner_event(1001))
        assert panel.active_page == 1 and len(select(panel, 'active').options) == 4
        await button(panel, 'Only chosen player').callback(owner_event(1002))
        assert panel.active_page == 0 and len(select(panel, 'active').options) == 6
        choice = select(panel, 'active')
        choice._values = [choice.options[0].value]
        await choice.callback(owner_event(1003))
        await button(panel, 'Edit selected').callback(owner_event(1004))
        assert panel.page == 'edit' and panel.roll == 'iq' and panel.draft['target'] == '42'
        panel.draft['value'] = 15
        await button(panel, 'Apply').callback(owner_event(1005))
        assert len(active(engine)) == 12 and active(engine)[0]['value'] == 15
        await button(panel, 'Active settings').callback(owner_event(1006))
        choice = select(panel, 'active')
        choice._values = [str(active(engine)[0]['id'])]
        await choice.callback(owner_event(1007))
        await button(panel, 'Remove selected').callback(owner_event(1008))
        assert len(active(engine)) == 11
        await button(panel, 'Clear player').callback(owner_event(1009))
        assert len(active(engine)) == 6 and all(r['target'] == '99' for r in active(engine))
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_active_manager_rejects_nonowners_and_refreshes_consumed_rigs(make_engine):
    async def check():
        engine = make_engine(draws(1))
        arm(engine, 'iq', 20)
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        await button(panel, 'Active settings').callback(owner_event())
        choice = select(panel, 'active')
        choice._values = [str(active(engine)[0]['id'])]
        await choice.callback(owner_event(1001))
        await button(panel, 'Remove selected').callback(owner_event(1002, owner=2))
        assert len(active(engine)) == 1
        engine.avatar(42, 100)
        await panel.refresh_message()
        assert panel.selected_id is None and select(panel, 'active').disabled
        embed = panel.message.edit.await_args.kwargs['embed']
        assert embed.title.endswith('0')
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_every_stat_is_selectable_and_all_manager_pages_fit_discord_limits(make_engine):
    async def check():
        engine = make_engine()
        for target in range(42, 92):
            arm(engine, 'iq', 100, 'good', target=target)
            arm(engine, 'stat1', 20, target=target)
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        assert set(f'stat{i}' for i in range(1, 7)) <= {option.value for option in select(panel, 'roll').options}
        assert len(ROLLS) <= 25
        panel.page = 'active'
        for page in range(13):
            panel.active_page = page
            panel.build_items()
            payload = panel.to_components()
            assert len(payload) <= 5
            assert len(panel.embed()) <= 6000
            assert len({child.custom_id for child in panel.children}) == len(panel.children)
            for row in payload:
                assert len(row['components']) <= 5
                for component in row['components']:
                    if component['type'] == 3:
                        assert 1 <= len(component['options']) <= 25
                        assert all(len(option['label']) <= 100 and len(option.get('description', '')) <= 100
                                   for option in component['options'])
        with pytest.raises(RollError, match='100 settings'):
            arm(engine, 'nation', 76, target=100)
        panel.stop()
        await bot.close()
    asyncio.run(check())
