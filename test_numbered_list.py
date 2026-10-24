"""Large equal-chance lists have stable numbering and use one real die."""

import asyncio
import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ua_bot.builder_ui import BuilderInput, BuilderPanel, ListInput, HELP
from ua_bot.client import DiceBot
from ua_bot.custom import MAX_LIST_OPTIONS, condition, list_options, new_field
from ua_bot.custom_ui import character_embeds
from ua_bot.errors import RollError
from ua_bot.rigger_ui import PanelInput, RiggerPanel, change_value
from test_custom_commands import command, die, rig, run
from test_custom_ui import current, payload_limits, submit
from test_rigger_panel import button, owner_event, select


def numbered(options, name='Treasure', **extra):
    return {'id': new_field(), 'name': name, 'type': 'list', 'options': options, **extra}


def test_thousands_of_options_draw_one_die_and_use_the_final_controlled_number(make_engine):
    options = [f'Item {i}' for i in range(1, 3_201)]
    entry = numbered(options)
    extra = die('Bonus', '1d6', when=condition('Treasure == 3200', [entry], 1))
    engine = make_engine([1, 4, 1])
    cmd = command(engine, [entry, extra])
    rig(engine, cmd, 'force', 3200, part=entry['id'])
    result = run(engine, cmd)
    item, bonus = result['character']['results']
    assert (item['type'], item['number'], item['outcome'], item['dice']['expression']) == (
        'list', 3200, 'Item 3200', '1d3200')
    assert bonus['name'] == 'Bonus' and bonus['dice']['total'] == 4
    assert engine.rng.calls == 2
    assert run(engine, cmd) == {**result, 'replayed': True}
    assert engine.rng.calls == 2
    ordinary = run(engine, cmd, request=801, player=43)['character']
    assert [part['name'] for part in ordinary['results']] == ['Treasure']
    assert ordinary['results'][0]['number'] == 1
    field = character_embeds(result['character'])[0].fields[0]
    assert field.name == 'Treasure'
    assert field.value == '**3200. Item 3200**\nRoll: `1d3200` → `3200`'
    assert not re.search(r'\brig(?:s|ging|ger)?\b', field.value, re.IGNORECASE)


def test_numbered_list_editor_appends_beyond_one_modal_and_renumbers(make_engine):
    async def check():
        bot = DiceBot(make_engine([1])); bot.sync_custom_guild = AsyncMock()
        original = command(bot.engine, entries=[], publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(original['id']))
        await button(panel, 'Add outcome').callback(current(3000))
        picker = panel.children[0]; picker._values = ['list']
        choice = current(3001); await picker.callback(choice)
        form = choice.response.send_modal.await_args.args[0]
        assert set(form.fields) == {'name', 'options', 'when'}
        await submit(form, dict(name='Treasure', options='Sword\nShield', when=''), current(3002))
        assert panel.command['draft']['entries'][0]['options'] == ['Sword', 'Shield']
        await button(panel, 'List options').callback(current(3003))
        assert panel.page == 'list'
        for batch in range(5):
            lines = '\n'.join(f'Item {batch * 200 + i}' for i in range(1, 201))
            assert len(lines) < 4000
            await submit(ListInput(panel, 'append'), {'options': lines}, current(3010 + batch))
        assert len(panel.selected_list()['options']) == 1002
        assert panel.list_page == (1002 - 1) // 15
        payload_limits(panel)
        assert '1d1002' in str(panel.embed().to_dict())
        await submit(ListInput(panel, 'replace'), {'number': '1002', 'option': 'Final prize'}, current(3020))
        assert panel.selected_list()['options'][-1] == 'Final prize'
        await submit(ListInput(panel, 'remove'), {'numbers': '1, 3-4'}, current(3021))
        assert panel.selected_list()['options'][0] == 'Shield'
        assert len(panel.selected_list()['options']) == 999
        assert panel.selected_list()['options'][-1] == 'Final prize'
        await button(panel, 'Previous').callback(current(3022))
        payload_limits(panel)
        await button(panel, 'Back').callback(current(3023))
        assert panel.page == 'edit'
        edit = BuilderInput(panel, 'edit')
        assert 'options' not in edit.fields and 'dice' not in edit.fields
        await button(panel, 'Publish').callback(current(3024))
        response = current(3025, user=42, permissions=())
        await bot.run_custom(response, identity=original['id'])
        fields = response.edit_original_response.await_args.kwargs['embed'].to_dict()['fields']
        assert fields[0]['value'] == '**1. Shield**\nRoll: `1d999` → `1`'
        assert 'rigger' not in json.dumps(fields).lower()
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_list_forms_reject_invalid_changes_without_modifying_draft(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        saved = command(bot.engine, [numbered(['One', 'Two'])], publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(saved['id']))
        await button(panel, 'List options').callback(current(3100))
        for action, values, message in [
            ('append', {'options': 'two'}, 'different text'),
            ('replace', {'number': '3', 'option': 'Three'}, '1 to 2'),
            ('remove', {'numbers': '1-2'}, 'at least one')]:
            failed = current(3101)
            await submit(ListInput(panel, action), values, failed)
            sent = failed.followup.send.await_args or failed.response.send_message.await_args
            assert message in sent.args[0]
            assert bot.customs.get(100, saved['id']) == saved
        stale = ListInput(panel, 'append')
        doc = copy.deepcopy(saved['draft']); doc['title'] = 'Updated elsewhere'
        newer = bot.customs.save(9, 100, saved['id'], saved['revision'], doc, is_manager=True)
        failed = current(3102)
        await submit(stale, {'options': 'Three'}, failed)
        assert bot.customs.get(100, saved['id']) == newer
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_list_limits_and_duplicate_names_are_checked_at_save(make_engine):
    assert list_options('Sword\n\nShield') == ['Sword', 'Shield']
    with pytest.raises(RollError, match='4,000'):
        list_options('X' * 4001)
    engine = make_engine()
    cmd = command(engine, publish=False)
    for options, reason in [
        (['Same', 'same'], 'different text'),
        (['X' * 201], '1–200'),
        (['x'] * (MAX_LIST_OPTIONS + 1), '10,000'),
        (['a' * 200 + str(i) for i in range(2_501)], '500,000')]:
        draft = copy.deepcopy(cmd['draft']); draft['entries'] = [numbered(options)]
        with pytest.raises(RollError, match=reason):
            engine.customs.save(9, 100, cmd['id'], cmd['revision'], draft, is_manager=True)
    assert engine.customs.get(100, cmd['id']) == cmd
    assert not re.search(r'\brig(?:s|ging|ger)?\b', HELP, re.IGNORECASE)


def test_private_panel_accepts_any_list_number_without_dropdown_limit(make_engine):
    async def check():
        engine = make_engine([1]); entry = numbered([f'Item {i}' for i in range(1, 501)])
        cmd = command(engine, [entry]); bot = DiceBot(engine)
        panel = RiggerPanel(bot); panel.message = SimpleNamespace(edit=AsyncMock())
        await button(panel, 'Custom rolls').callback(owner_event(3200))
        picker = select(panel, 'custom'); picker._values = [f"custom:{cmd['id']}"]
        await picker.callback(owner_event(3201))
        picker = select(panel, 'roll'); picker._values = [entry['id']]
        await picker.callback(owner_event(3202))
        picker = select(panel, 'mode'); picker._values = ['force']
        await picker.callback(owner_event(3203))
        assert not any(getattr(item, 'kind', '') == 'outcome' for item in panel.children)
        modal = PanelInput(panel, 'value'); modal.field._value = '500'
        await modal.on_submit(owner_event(3204))
        assert 'Force #500 · Item 500' in str(panel.embed().to_dict())
        panel.draft['target'] = '42'
        await button(panel, 'Apply').callback(owner_event(3205))
        assert change_value(engine.store.controls(1)['rules'][0], engine.customs) == 'Force #500 · Item 500'
        assert run(engine, cmd)['character']['results'][0]['outcome'] == 'Item 500'
        panel.stop(); await bot.close()
    asyncio.run(check())
