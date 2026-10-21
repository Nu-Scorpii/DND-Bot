"""Single-field rolls, explicit modifiers, and the compact private editor."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from conftest import ScriptedRNG
from test_discord import interaction
from test_rigger_panel import button, owner_event
from ua_bot.client import DiceBot
from ua_bot.dice import prepare, values_of
from ua_bot.display import length, roll_messages
from ua_bot.errors import RollError
from ua_bot.rigger_ui import PanelSelect, RiggerPanel


@pytest.mark.parametrize('expression,line,total', [
    ('1d100', None, 50), ('1d100+2', 'Bonus: `+2`', 52),
    ('1d100-2', 'Penalty: `-2`', 48), ('1D100 − 2', 'Penalty: `-2`', 48),
    ('1d100+0', 'Modifier: `+0`', 50),
])
def test_modifier_is_visible_and_added_once(expression, line, total):
    result = prepare(expression).roll(ScriptedRNG([50]))
    public = '\n'.join(roll_messages(result, 'Hero'))
    assert 'Rolls: `50`' in public and f'**Total: {total}**' in public
    if line:
        assert public.count(line) == 1
    else:
        assert all(word not in public for word in ('Bonus:', 'Penalty:', 'Modifier:'))


def test_multiple_modifiers_show_each_term_and_net_without_double_counting():
    result = prepare('2d20+2-3+4').roll(ScriptedRNG([8, 10]))
    public = '\n'.join(roll_messages(result))
    assert 'Modifiers: `+2 -3 +4` · net `+3`' in public
    assert '**Total: 21**' in public


@pytest.mark.parametrize('expression,die', [('1d100+2', 48), ('1d100-2', 52)])
def test_force_still_means_final_total_including_modifier(expression, die, make_engine):
    engine = make_engine([8, 9])
    engine.configure(1, is_dm=True, mode='force', value=50, target=42, uses=2)
    first = engine.roll(42, 100, expression, request_id=500)
    assert first['dice']['total'] == 50 and values_of(first['dice']) == [die]
    assert engine.control_status(1, is_dm=True)['rules'][0]['remaining'] == 1
    assert engine.roll(42, 100, expression, request_id=500)['dice'] == first['dice']
    assert engine.rng.calls == 1
    assert engine.roll(42, 200, expression, request_id=501)['dice']['total'] == 50
    assert not engine.control_status(1, is_dm=True)['rules']


@pytest.mark.parametrize('mode,value,face', [('good', 50, 75), ('bad', 50, 25), ('shift', 5, 55), ('shift', -5, 45)])
@pytest.mark.parametrize('modifier', [2, -2])
def test_luck_and_shift_keep_the_declared_modifier(mode, value, face, modifier, make_engine):
    engine = make_engine([50])
    engine.configure(1, is_dm=True, mode=mode, value=value, target=42)
    result = engine.roll(42, 100, f'1d100{modifier:+d}')['dice']
    assert values_of(result) == [face] and result['total'] == face + modifier
    public = '\n'.join(roll_messages(result))
    assert f"{'Bonus' if modifier > 0 else 'Penalty'}: `{modifier:+d}`" in public
    assert all(word not in public.lower() for word in ('rigger', 'force', 'luck', 'shift'))


@pytest.mark.parametrize('expression,values,total', [
    ('2d[0,1,1,5]+2', [1, 5], 8),
    ('1d[-5,8]-2d[-3,0,2]+1d3-2', [8, -3, 2, 3], 10),
    ('3d20kh1+2', [1, 15, 8], 17),
    ('3D20KL1-2', [1, 15, 8], -1),
    ('2d[0,5,10]kh1+2', [0, 10], 12),
])
def test_advanced_rolls_still_fit_the_single_field(expression, values, total):
    plan = prepare(expression)
    result = plan.roll(ScriptedRNG(values))
    assert result['total'] == total
    assert values_of(result) == values


def test_inline_faces_keep_weight_case_and_json_delimiters():
    plan = prepare('2d["Sun","rain","rain","a]d[Kh1"]')
    rng = ScriptedRNG(['Sun', 'a]d[Kh1'])
    result = plan.roll(rng)
    assert result['total'] is None and values_of(result) == ['Sun', 'a]d[Kh1']
    assert rng.domains == [('Sun', 'rain', 'rain', 'a]d[Kh1')] * 2


@pytest.mark.parametrize('expression', [
    '1d100++2', '1d100-2.5', '1d[1,true]', '1d[]', '1d["rain"]+2',
    '1d["rain"]kh1', '1d[1,2', '1d20+2kh1', '1d20kh1+1d6',
    '1d20kh1+1d6kl1', '1d100+1000000001',
])
def test_bad_input_does_not_draw_or_spend_a_rig(expression, make_engine):
    engine = make_engine()
    engine.configure(1, is_dm=True, mode='force', value=50, target=42)
    with pytest.raises(RollError):
        engine.roll(42, 100, expression)
    assert engine.rng.calls == 0
    assert engine.control_status(1, is_dm=True)['rules'][0]['remaining'] == 1


def test_inline_custom_faces_still_work_with_rigger(make_engine):
    engine = make_engine([0, 'Sun'])
    engine.configure(1, is_dm=True, mode='force', value=7, target=42)
    result = engine.roll(42, 100, '1d[0,1,1,5]+2')['dice']
    assert result['total'] == 7 and values_of(result) == [5]
    engine.configure(1, is_dm=True, mode='face', value='Rain', target=42)
    result = engine.roll(42, 100, '1d["Sun","Rain"]')['dice']
    assert result['total'] is None and values_of(result) == ['Rain']


def test_actual_single_field_callback_delivers_rigged_rolls_privately(make_engine):
    async def check():
        engine = make_engine([8, 9, 10])
        bot = DiceBot(engine)
        bot.notify_creator = AsyncMock()
        command = bot.tree.get_command('roll')
        payload = command.to_dict(bot.tree)
        assert [option['name'] for option in payload['options']] == ['dice']
        engine.configure(1, is_dm=True, mode='force', value=50, target=2, uses=2)
        for request, expression, line in [(900, '1d100+2', 'Bonus: `+2`'), (901, '1d100-2', 'Penalty: `-2`')]:
            event = interaction(request)
            await command.callback(event, dice=expression)
            public = event.edit_original_response.await_args.kwargs['content']
            assert '**Total: 50**' in public and line in public
            assert 'rigger' not in public.lower()
            assert not event.followup.send.await_args_list
        event = interaction(902)
        await command.callback(event)
        assert '**Total: 10**' in event.edit_original_response.await_args.kwargs['content']
        await bot.close()
    asyncio.run(check())


def test_compact_editor_retains_controls_and_moves_details_to_manager(make_engine):
    async def check():
        engine = make_engine([8])
        engine.configure(1, is_dm=True, mode='force', value=50, target=42, uses=3)
        engine.roll(42, 100, '1d100+2')
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        embed = panel.embed()
        assert [field.name for field in embed.fields] == ['Player', 'Roll', 'Change', 'Duration']
        assert embed.fields[2].value == 'Force 50' and embed.fields[3].value == '2 uses left'
        assert '1 active / 1 for this player' in embed.footer.text
        assert not any(isinstance(child, PanelSelect) and child.disabled for child in panel.children)
        labels = {child.label for child in panel.children if isinstance(child, discord.ui.Button)}
        assert labels == {'Choose player', 'Target me', 'Enter value', 'Duration', 'Apply', 'Active settings', 'Help', 'Custom rolls'}
        await button(panel, 'Active settings').callback(owner_event())
        manager = str(panel.embed().to_dict())
        assert '2 uses left' in manager and 'Last affected roll' in manager
        assert button(panel, 'Clear all') and button(panel, 'Refresh')
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_inline_text_mentions_and_long_results_remain_safe():
    expression = '100d["@everyone **Rain**"]'
    public = roll_messages(prepare(expression).roll(ScriptedRNG(['@everyone **Rain**'] * 100)))
    assert all(length(part) <= 2000 and '@everyone' not in part for part in public)
    assert sum(part.count('Rain') for part in public) == 101
