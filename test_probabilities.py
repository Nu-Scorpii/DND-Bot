"""Named chance outcomes have exact odds and never leak an internal die."""

import asyncio
import copy
import json
import re
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ua_bot.builder_ui import BuilderInput, BuilderPanel, HELP as BUILDER_HELP, class_entries
from ua_bot.client import DiceBot
from ua_bot.custom import condition, new_field, odds, odds_text
from ua_bot.custom_ui import character_embeds
from ua_bot.errors import RollError
from ua_bot.rigger_ui import RiggerPanel, change_value
from test_custom_commands import command, die, rig, run, table
from test_custom_ui import current, payload_limits, submit
from test_rigger_panel import button, owner_event, select


def probability(name='Nation', source='Fire | 50%\nWater | 50%', **extra):
    return dict(id=new_field(), name=name, type='probability', outcomes=odds(source, percentages=True), **extra)


@pytest.mark.parametrize('source', ['Only | 100%', 'Fire | 25\nWater | 75',
    'Rare | 0.0001%\nCommon | 99.9999%', 'First | 33.3333%\nSecond | 33.3333%\nThird | 33.3334%',
    'Fire | 12.5%\nWater | 37.5%\nEarth | 50%'])
def test_percentages_edit_without_rounding_or_renormalization(source, make_engine):
    entry = probability(source=source)
    text = odds_text(entry['outcomes'], percentages=True)
    assert odds(text, percentages=True) == entry['outcomes']
    cmd = command(make_engine(), [entry])
    assert cmd['published']['entries'][0] == entry


@pytest.mark.parametrize('source', ['A | 20%\nB | 20%', 'A | 70%\nB | 40%', 'A | 101%',
    'A | 33.3333%\nB | 33.3333%\nC | 33.3333%', 'A | 0%\nB | 100%',
    'A | -1%\nB | 101%', 'A | 0.00001%\nB | 99.99999%', 'A | 1e2%', 'A | nan'])
def test_invalid_probabilities_are_never_silently_normalized(source):
    with pytest.raises(RollError): odds(source, percentages=True)


def test_exact_distribution_counts_and_name_only_receipts(make_engine):
    engine = make_engine(range(1, 9))
    cmd = command(engine, [probability(source='Fire | 12.5%\nWater | 37.5%\nEarth | 50%')])
    selected = []
    for sample in range(8):
        result = run(engine, cmd, request=sample + 1000)['character']['results'][0]
        assert set(result) == {'id', 'name', 'type', 'outcome'}
        selected.append(result['outcome'])
    assert Counter(selected) == {'Fire': 1, 'Water': 3, 'Earth': 4}
    assert engine.rng.calls == 8


def test_clan_is_a_standalone_outcome_type_with_private_creator_override(make_engine):
    entry = probability('Clan', 'Uchiha | 5%\nHyuga | 10%\nUzumaki | 15%\nOther clan | 70%')
    engine = make_engine([20, 20])
    cmd = command(engine, [entry])
    rig(engine, cmd, 'force', 1, part=entry['id'])
    chosen = run(engine, cmd)['character']
    ordinary = run(engine, cmd, request=801, player=43)['character']
    assert chosen['results'][0]['outcome'] == 'Uchiha'
    assert ordinary['results'][0]['outcome'] == 'Other clan'
    assert chosen['results'][0].keys() == {'id', 'name', 'type', 'outcome'}
    assert [f.name for f in character_embeds(chosen)[0].fields] == ['Clan']
    assert character_embeds(chosen)[0].fields[0].value == '**Uchiha**'
    assert not re.search(r'\brig(?:s|ging|ger)?\b', BUILDER_HELP, re.IGNORECASE)


def test_probability_and_all_three_requested_dice_groups_work_together(make_engine):
    engine = make_engine([2, 4, 1, 2, 3, 4, 5, 6, 18])
    cmd = command(engine, [probability(), die('Origin', '1d5'), die('Stats', '6d20'), die('Strength', '1d20+2')])
    result = run(engine, cmd)['character']
    embed = character_embeds(result)[0]
    assert [f.name for f in embed.fields] == ['Nation', 'Origin', 'Stats', 'Strength']
    assert embed.fields[0].value == '**Water**'
    assert '`1` · `2` · `3` · `4` · `5` · `6`' in embed.fields[2].value
    assert 'Bonus: `+2`' in embed.fields[3].value and '**Total: 20**' in embed.fields[3].value


@pytest.mark.parametrize('mode,value,values,expected', [
    ('force', 2, [1], 'Water'), ('shift', 1, [1], 'Water'),
    ('good', 100, [1], 'Water'), ('bad', 100, [2], 'Fire'),
    ('extra_high', 3, [1, 1, 2, 1], 'Water'), ('extra_low', 3, [2, 2, 1, 2], 'Fire')])
def test_all_numeric_rigger_modes_change_named_results_privately(mode, value, values, expected, make_engine):
    engine = make_engine(values)
    entry = probability(); cmd = command(engine, [entry])
    rig(engine, cmd, mode, value, part=entry['id'])
    result = run(engine, cmd)
    assert result['character']['results'][0]['outcome'] == expected
    assert 'dice' not in result['character']['results'][0]
    assert character_embeds(result['character'])[0].fields[0].value == '**' + expected + '**'
    assert not engine.store.controls(1)['rules']


def test_named_conditions_use_final_rigged_choice_and_skip_unneeded_dice(make_engine):
    engine = make_engine([1, 17, 1])
    nation = probability()
    strength = die('Strength', when=condition('Nation == Water', [nation], 1))
    cmd = command(engine, [nation, strength])
    rig(engine, cmd, 'force', 2, part=nation['id'])
    assert len(run(engine, cmd)['character']['results']) == 2
    assert len(run(engine, cmd, request=801)['character']['results']) == 1
    assert engine.rng.calls == 3
    with pytest.raises(RollError, match='== or !='):
        condition('Nation > 1', [nation], 1)


def test_probability_receipts_and_duration_survive_restart(tmp_path, make_engine):
    path = tmp_path / 'probabilities.sqlite3'
    engine = make_engine([1], path)
    cmd = command(engine, [probability()])
    rig(engine, cmd, 'good', 100, uses=2)
    first = run(engine, cmd)
    engine.store.close()
    restarted = make_engine([], path)
    duplicate = run(restarted, cmd)
    assert duplicate == {**first, 'replayed': True}
    assert restarted.rng.calls == 0 and restarted.store.controls(1)['rules'][0]['remaining'] == 1
    assert character_embeds(duplicate['character'])[0].fields[0].value == '**Water**'


def test_legacy_relative_weights_keep_original_draw_display(make_engine):
    engine = make_engine([2])
    cmd = command(engine, [table(source='Rare | 1\nCommon | 3')])
    character = run(engine, cmd)['character']
    field = character_embeds(character)[0].fields[0]
    assert '**Common**' in field.value and 'Rolls: `2`' in field.value and '**Total: 2**' in field.value


def test_forged_probability_definition_cannot_imply_unsupported_precision(make_engine):
    engine = make_engine(); cmd = command(engine, publish=False)
    doc = copy.deepcopy(cmd['draft'])
    entry = probability(); entry['outcomes'] = [{'name': 'A', 'weight': 1}, {'name': 'B', 'weight': 2}]
    doc['entries'] = [entry]
    with pytest.raises(RollError, match='four decimal places'):
        engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
    assert engine.customs.get(100, cmd['id']) == cmd


def test_probability_builder_buttons_form_edit_and_public_command(make_engine):
    async def check():
        bot = DiceBot(make_engine([1])); bot.sync_custom_guild = AsyncMock()
        cmd = command(bot.engine, entries=[], publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(cmd['id']))
        await button(panel, 'Add outcome').callback(current(2000))
        assert panel.page == 'outcomes'
        payload_limits(panel)
        picker = panel.children[0]; picker._values = ['probability']
        pick = current(2001); await picker.callback(pick)
        form = pick.response.send_modal.await_args.args[0]
        assert 'dice' not in form.fields and len(form.to_components()) <= 5
        await submit(form, dict(name='Nation', outcomes='Fire | 12.5%\nWater | 87.5%', when=''), current(2002))
        assert panel.page == 'edit' and panel.command['draft']['entries'][0]['type'] == 'probability'
        payload_limits(panel)
        edit = BuilderInput(panel, 'edit')
        assert edit.fields['outcomes'].default == 'Fire | 12.5%\nWater | 87.5%'
        await button(panel, 'Publish').callback(current(2003))
        response = current(2004, user=42, permissions=())
        await bot.run_custom(response, identity=cmd['id'])
        payload = response.edit_original_response.await_args.kwargs['embed'].to_dict()
        assert payload['fields'][0]['value'] == '**Fire**'
        assert all(term not in json.dumps(payload) for term in ('Rolls:', 'Total:', '1d8', 'ticket', 'rigger'))
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_class_subclass_probabilities_follow_the_final_class_result(make_engine):
    entries = class_entries('Class', 'Fighter | 60%\nMage | 40%', 'Subclass',
        'Fighter | Champion | 70%\nFighter | Guardian | 30%\n'
        'Mage | Evoker | 25%\nMage | Illusionist | 75%', [])
    engine = make_engine([1, 1, 1, 1])
    cmd = command(engine, entries)
    parent, fighter, mage = cmd['published']['entries']
    assert fighter['when'] == {'field': parent['id'], 'op': '==', 'value': 'Fighter'}
    assert mage['when'] == {'field': parent['id'], 'op': '==', 'value': 'Mage'}
    rig(engine, cmd, 'force', 4, part=parent['id'])
    rig(engine, cmd, 'force', 4, part=mage['id'])
    forced = run(engine, cmd)['character']
    assert [(item['name'], item['outcome']) for item in forced['results']] == [
        ('Class', 'Mage'), ('Subclass', 'Illusionist')]
    assert [item['id'] for item in forced['results']] == [parent['id'], mage['id']]
    assert [set(item) for item in forced['results']] == [
        {'id', 'name', 'type', 'outcome'}, {'id', 'name', 'type', 'outcome'}]
    assert [(field.name, field.value) for field in character_embeds(forced)[0].fields] == [
        ('Class', '**Mage**'), ('Subclass', '**Illusionist**')]
    ordinary = run(engine, cmd, request=801, player=43)['character']
    assert [(item['name'], item['outcome']) for item in ordinary['results']] == [
        ('Class', 'Fighter'), ('Subclass', 'Champion')]
    assert engine.rng.calls == 4


@pytest.mark.parametrize('classes,subclasses,reason', [
    ('Fighter | 60%\nMage | 40%', 'Fighter | Champion | 100%', 'every class'),
    ('Fighter | 60%\nMage | 40%', 'Fighter | Champion | 100%\nRogue | Thief | 100%\nMage | Evoker | 100%', 'Unknown class'),
    ('Fighter | 60%\nMage | 40%', 'Fighter | Champion | 80%\nMage | Evoker | 100%', '80%'),
    ('Fighter | 60%\nMage | 40%', 'Fighter | Champion | 100%\nMage | Evoker | 80%\nMage | Illusionist | 30%', '110%'),
    ('Fighter | 60%\nMage | 40%', 'Fighter | Champion | 100%\nMage | Evoker | 100%\nMage | Evoker | 0%', 'above zero'),
])
def test_class_form_requires_complete_separate_subclass_odds(classes, subclasses, reason):
    with pytest.raises(RollError, match=reason):
        class_entries('Class', classes, 'Subclass', subclasses, [])


def test_class_subclass_builder_form_saves_atomically_and_publishes(make_engine):
    async def check():
        bot = DiceBot(make_engine([1, 1])); bot.sync_custom_guild = AsyncMock()
        cmd = command(bot.engine, entries=[], publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(cmd['id']))
        await button(panel, 'Add outcome').callback(current(2300))
        picker = panel.children[0]; picker._values = ['classes']
        pick = current(2301); await picker.callback(pick)
        form = pick.response.send_modal.await_args.args[0]
        assert len(form.to_components()) == 4
        values = dict(name='Class', outcomes='Fighter | 60%\nMage | 40%', subclass_name='Subclass',
                      subclasses='Fighter | Champion | 100%\nMage | Evoker | 100%')
        invalid = BuilderInput(panel, 'classes')
        failed = current(2302)
        await submit(invalid, {**values, 'subclasses': 'Fighter | Champion | 100%'}, failed)
        assert bot.customs.get(100, cmd['id']) == cmd
        assert 'every class' in failed.followup.send.await_args.args[0]
        await submit(form, values, current(2303))
        assert panel.page == 'edit' and len(panel.command['draft']['entries']) == 3
        payload_limits(panel)
        branch = panel.command['draft']['entries'][-1]
        panel.selected = branch['id']
        edited = BuilderInput(panel, 'edit')
        assert edited.fields['display_name'].default == 'Subclass'
        await button(panel, 'Publish').callback(current(2304))
        response = current(2305, user=42, permissions=())
        await bot.run_custom(response, identity=cmd['id'])
        payload = response.edit_original_response.await_args.kwargs['embed'].to_dict()
        assert [(field['name'], field['value']) for field in payload['fields']] == [
            ('Class', '**Fighter**'), ('Subclass', '**Champion**')]
        assert not re.search(r'\brig(?:s|ging|ger)?\b', json.dumps(payload), re.IGNORECASE)
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_invalid_total_does_not_save_and_probability_picker_obeys_permissions(make_engine):
    async def check():
        bot = DiceBot(make_engine()); cmd = command(bot.engine, entries=[], publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(cmd['id']))
        await button(panel, 'Add outcome').callback(current(2100))
        form = BuilderInput(panel, 'entry', entry_type='probability')
        bad = current(2101)
        await submit(form, dict(name='Nation', outcomes='Fire | 20%\nWater | 20%', when=''), bad)
        assert '40%' in bad.followup.send.await_args.args[0] and '100%' in bad.followup.send.await_args.args[0]
        assert bot.customs.get(100, cmd['id']) == cmd
        picker = panel.children[0]; picker._values = ['probability']
        revoked = current(2102, permissions=('moderate_members',), owner=8)
        await picker.callback(revoked)
        revoked.response.send_modal.assert_not_awaited()
        assert revoked.response.send_message.await_args.kwargs['ephemeral']
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_rigger_force_picker_and_active_status_show_the_named_outcome(make_engine):
    async def check():
        engine = make_engine([1]); entry = probability(); cmd = command(engine, [entry])
        bot = DiceBot(engine); panel = RiggerPanel(bot); panel.message = SimpleNamespace(edit=AsyncMock())
        await button(panel, 'Custom rolls').callback(owner_event(2200))
        picker = select(panel, 'custom'); picker._values = [f"custom:{cmd['id']}"]
        await picker.callback(owner_event(2201))
        picker = select(panel, 'roll'); picker._values = [entry['id']]
        await picker.callback(owner_event(2202))
        picker = select(panel, 'mode'); picker._values = ['force']
        await picker.callback(owner_event(2203))
        picker = select(panel, 'outcome')
        assert [o.label for o in picker.options] == ['Fire', 'Water']
        assert all(o.description == 'Select this named outcome.' for o in picker.options)
        picker._values = ['2']; await picker.callback(owner_event(2204))
        assert panel.embed().fields[2].value == 'Force Water'
        await button(panel, 'Apply').callback(owner_event(2205))
        await button(panel, 'Active settings').callback(owner_event(2206))
        assert 'Force Water' in str(panel.embed().to_dict())
        payload_limits(panel)
        engine.customs.visibility(9, 100, cmd['id'], cmd['revision'], 'unpublish', is_manager=True)
        assert change_value(engine.store.controls(1)['rules'][0], engine.customs) == 'Force 2'
        panel.stop(); await bot.close()
    asyncio.run(check())
