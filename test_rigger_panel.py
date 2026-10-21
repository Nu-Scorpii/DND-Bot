import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.controls import panel_setting
from ua_bot.errors import RollError
from ua_bot.rigger_ui import PanelInput, PanelSelect, PRESETS, RiggerPanel
from test_avatar import draws
from test_discord import interaction


def owner_event(request=1000, owner=1, guild=None):
    event = interaction(request)
    event.guild_id = guild
    event.user.id = owner
    event.response.edit_message = AsyncMock()
    event.response.send_modal = AsyncMock()
    return event


def button(panel, label):
    return next(item for item in panel.children if isinstance(item, discord.ui.Button) and item.label == label)


def select(panel, kind):
    return next(item for item in panel.children if isinstance(item, PanelSelect) and item.kind == kind)


def test_private_panel_is_opened_by_either_case_without_arming_a_roll(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        channel = MagicMock(spec=discord.DMChannel)
        channel.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        for index, command in enumerate(('!control', '!Control', '!rigger')):
            await bot.on_message(SimpleNamespace(id=800 + index, author=SimpleNamespace(id=1, bot=False),
                                                content=command, channel=channel))
        assert channel.send.await_count == 3
        assert bot.engine.store.setting(1)['mode'] == 'off'
        assert bot.rigger_panel.draft['target'] == '1'
        await bot.close()
    asyncio.run(check())


def test_server_messages_other_players_and_wrong_prefix_never_get_a_panel(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        dm = MagicMock(spec=discord.DMChannel)
        dm.send = AsyncMock()
        guild_channel = SimpleNamespace(send=AsyncMock())
        for author, channel, content in [(2, dm, '!rigger'), (1, guild_channel, '!rigger'),
                                         (1, dm, '!riggerish'), (1, dm, 'hello')]:
            await bot.on_message(SimpleNamespace(id=1000, author=SimpleNamespace(id=author, bot=False),
                                                content=content, channel=channel))
        assert bot.rigger_panel is None
        dm.send.assert_not_awaited()
        guild_channel.send.assert_not_awaited()
        await bot.close()
    asyncio.run(check())


def test_reopening_closes_old_panel_and_duplicate_delivery_does_not_reopen(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        channel = MagicMock(spec=discord.DMChannel)
        first_message = SimpleNamespace(edit=AsyncMock())
        channel.send = AsyncMock(side_effect=[first_message, SimpleNamespace(edit=AsyncMock())])
        event = SimpleNamespace(id=800, author=SimpleNamespace(id=1, bot=False), content='!rigger', channel=channel)
        await bot.on_message(event)
        old = bot.rigger_panel
        await bot.on_message(event)
        assert channel.send.await_count == 1
        event.id = 801
        await bot.on_message(event)
        assert old.is_finished() and all(item.disabled for item in old.children)
        first_message.edit.assert_awaited_once()
        await bot.close()
    asyncio.run(check())


def test_panel_and_modal_reject_nonowners_and_server_interactions(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = RiggerPanel(bot)
        panel.draft['value'] = 15
        modal = PanelInput(panel, 'value')
        modal.field._value = '19'
        before = dict(panel.draft)
        for event in (owner_event(owner=2), owner_event(guild=100)):
            assert not await panel.interaction_check(event)
            assert not await modal.interaction_check(event)
            await modal.on_submit(event)
            await button(panel, 'Apply').callback(event)
            assert event.response.send_message.await_args.kwargs['ephemeral']
            assert panel.draft == before
            assert bot.engine.store.setting(1)['mode'] == 'off'
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('author,is_dm', [(2, True), (1, False)])
def test_engine_has_an_independent_owner_and_dm_guard(author, is_dm, make_engine):
    engine = make_engine()
    with pytest.raises(RollError, match='private'):
        engine.configure(author, is_dm=is_dm, mode='force', value=20, target=42)
    with pytest.raises(RollError, match='private'):
        engine.control_status(author, is_dm=is_dm)
    assert engine.store.setting(1)['mode'] == 'off'


def test_panel_can_force_avatar_for_a_nonowner_across_servers(make_engine):
    async def check():
        engine = make_engine([4] + draws(1) + draws(76, bending=60, end=81))
        bot = DiceBot(engine)
        panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        scope = select(panel, 'roll')
        scope._values = ['bending']
        await scope.callback(owner_event(1000))
        preset = select(panel, 'outcome')
        preset._values = ['61']
        await preset.callback(owner_event(1001))
        modal = PanelInput(panel, 'target')
        modal.field._value = '<@!42>'
        bot.fetch_user = AsyncMock(return_value=SimpleNamespace(id=42, bot=False))
        event = owner_event(1002)
        event.response.is_done.return_value = True
        await modal.on_submit(event)
        assert panel.draft['target'] == '42'
        assert engine.store.setting(1)['mode'] == 'off'  # Draft is not armed.
        event = owner_event(1003)
        event.response.is_done.return_value = True
        await button(panel, 'Apply').callback(event)
        assert engine.roll(42, 200)['dice']['total'] == 4
        assert engine.avatar(9, 300)['character']['bender']
        assert engine.store.setting(1)['target'] == '42'
        # The targeted Nomad's natural 60 becomes 61; only then does element d3 roll.
        engine.rng.values = iter(draws(76, bending=60, end=3) + [12])
        character = engine.avatar(42, 400)['character']
        assert character['bender'] and character['bending'] == 'Earth bending'
        assert 'element' in character['rolls'] and 'combat' not in character['rolls']
        assert engine.store.setting(1)['mode'] == 'off'
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_roll_scope_cannot_accidentally_change_or_be_consumed_by_avatar(make_engine):
    engine = make_engine(draws(26) + [4])
    engine.configure(1, is_dm=True, mode='force', value=17, target='42', scope='roll')
    assert engine.avatar(42, 100)['character']['iq'] == 11
    assert engine.store.setting(1)['mode'] == 'force'
    assert engine.roll(42, 200)['dice']['total'] == 17


def test_new_controls_default_to_owner_and_do_not_change_another_player(make_engine):
    engine = make_engine([4, 5])
    engine.configure(1, is_dm=True, mode='force', value=17)
    assert engine.store.setting(1)['target'] == '1'
    assert engine.roll(42, 100)['dice']['total'] == 4
    assert engine.roll(1, 200)['dice']['total'] == 17


def test_panel_duplicate_apply_does_not_rearm_a_consumed_setting(make_engine):
    engine = make_engine([4, 5])
    arguments = dict(is_dm=True, mode='force', value=15, target=42, request_id=700)
    engine.configure(1, **arguments)
    assert engine.roll(42, 100)['dice']['total'] == 15
    engine.configure(1, **arguments)
    assert engine.store.setting(1)['mode'] == 'off'
    assert engine.roll(42, 200)['dice']['total'] == 5


def test_panel_rule_and_consumption_survive_restart(tmp_path, make_engine):
    path = tmp_path / 'panel.sqlite3'
    first = make_engine(path=path)
    first.configure(1, is_dm=True, mode='force', value=60, target=42, scope='avatar', part='bending')
    second = make_engine(draws(76, bending=61, end=81), path)
    assert second.avatar(42, 200)['character']['combat'] == 'Pressure point'
    third = make_engine(path=path)
    assert third.store.setting(1)['mode'] == 'off'


@pytest.mark.parametrize('nation', [1, 26, 51, 76])
@pytest.mark.parametrize('bending', [59, 60, 61])
def test_element_d3_is_only_for_nomads_strictly_above_60(nation, bending, make_engine):
    engine = make_engine(draws(nation, bending=bending, end=3))
    character = engine.avatar(42, 100)['character']
    has_element = nation == 76 and bending > 60
    assert ('element' in character['rolls']) is has_element
    assert character['bender'] is (bending > 60)
    assert engine.rng.calls == len(draws(nation, bending=bending, end=3))


@pytest.mark.parametrize('forced,natural,bender', [(60, 61, False), (61, 60, True)])
def test_modified_bending_uses_final_threshold_to_choose_d3_or_combat(forced, natural, bender, make_engine):
    scripted = draws(76, bending=61 if bender else 60, end=3)
    scripted[8] = natural
    engine = make_engine(scripted)
    engine.configure(1, is_dm=True, mode='force', value=forced, target=42, scope='avatar', part='bending')
    character = engine.avatar(42, 100)['character']
    assert character['bender'] is bender
    assert ('element' in character['rolls']) is bender
    assert ('combat' in character['rolls']) is (not bender)


def test_full_whole_character_luck_changes_only_needed_parts_and_persists(make_engine):
    engine = make_engine([1] + [1] * 6 + [1, 60, 1, 12])
    engine.configure(1, is_dm=True, mode='good', target=42, scope='avatar')
    character = engine.avatar(42, 100)['character']
    assert character['iq'] == 20 and character['stats'] == [20] * 6
    assert character['nation'] == 'Nomad' and character['bending'] == 'Earth bending'
    assert set(character['rolls']) == {'iq', 'stats', 'nation', 'bending', 'element', 'bending_strength'}
    assert character['bending_strength'] == 20
    assert engine.rng.calls == 11 and engine.store.setting(1)['mode'] == 'good'


@pytest.mark.parametrize('setting', [
    dict(mode='force', value=101, scope='avatar', part='bending'),
    dict(mode='force', value=15, scope='avatar'),
    dict(mode='shift', value=3, scope='avatar'),
    dict(mode='face', value='rain', scope='avatar', part='iq'),
    dict(mode='force', value='oops'), dict(mode='force', value=15, target='server'),
])
def test_invalid_panel_draft_does_not_replace_active_control(setting, make_engine):
    engine = make_engine()
    engine.configure(1, is_dm=True, mode='force', value=15, target=42)
    before = engine.store.setting(1)
    with pytest.raises(RollError):
        engine.configure(1, is_dm=True, **setting)
    assert engine.store.setting(1) == before


def test_real_discord_panel_and_modal_payloads_fit_component_limits(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = RiggerPanel(bot)
        for mode in ('force', 'shift', 'good', 'bad', 'face'):
            for roll in ('roll', 'avatar', *PRESETS):
                panel.roll, panel.draft['mode'] = roll, mode
                panel.build_items()
                payload = panel.to_components()
                assert len(payload) <= 5
                assert len({child.custom_id for child in panel.children}) == len(panel.children)
                for row in payload:
                    assert len(row['components']) <= 5
                    for component in row['components']:
                        if component['type'] == 3:
                            assert 1 <= len(component['options']) <= 25
                            assert all(len(option.get('description', '')) <= 100 for option in component['options'])
        panel.draft.update(scope='roll', part=None, mode='force')
        for kind in ('target', 'value'):
            modal = PanelInput(panel, kind)
            payload = modal.to_dict()
            assert payload['title'] and len(payload['components']) == 1
            modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_expired_panel_and_stale_modal_cannot_change_settings(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = RiggerPanel(bot)
        modal = PanelInput(panel, 'value')
        modal.field._value = '20'
        panel.message = SimpleNamespace(edit=AsyncMock())
        await panel.expire()
        assert panel.is_finished() and all(item.disabled for item in panel.children)
        await modal.on_submit(owner_event())
        await button(panel, 'Apply').callback(owner_event(1001))
        assert bot.engine.store.setting(1)['mode'] == 'off'
        assert 'Active settings are kept' in panel.message.edit.await_args.kwargs['embed'].description
        await bot.close()
    asyncio.run(check())


def test_value_modal_keeps_the_number_in_draft_until_apply(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = RiggerPanel(bot)
        modal = PanelInput(panel, 'value')
        modal.field._value = '-4'
        await modal.on_submit(owner_event())
        assert panel.draft['value'] == -4 and panel.draft['mode'] == 'force'
        assert bot.engine.store.setting(1)['mode'] == 'off'
        assert select(panel, 'mode').options[0].default
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('failed_lookup', ['not_found', 'bot'])
def test_player_modal_rejects_wrong_ids_without_changing_target(failed_lookup, make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = RiggerPanel(bot)
        modal = PanelInput(panel, 'target')
        modal.field._value = '42'
        if failed_lookup == 'bot':
            bot.fetch_user = AsyncMock(return_value=SimpleNamespace(id=42, bot=True))
        else:
            error = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'User not found')
            bot.fetch_user = AsyncMock(side_effect=error)
        event = owner_event()
        event.response.is_done.return_value = True
        await modal.on_submit(event)
        assert panel.draft['target'] == '1'
        assert bot.engine.store.setting(1)['mode'] == 'off'
        assert event.followup.send.await_args.kwargs['ephemeral']
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_panel_refresh_after_public_roll_does_not_leak_private_state(make_engine):
    async def check():
        engine = make_engine([4])
        engine.configure(1, is_dm=True, mode='force', value=15, target=2)
        bot = DiceBot(engine)
        panel = bot.rigger_panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        event = interaction()
        await bot.run_roll(event)
        public = event.edit_original_response.await_args.kwargs['content']
        assert '**Total: 15**' in public and 'Rigger' not in public and 'force' not in public
        private = panel.message.edit.await_args.kwargs['embed'].to_dict()
        assert '0 active / 0 for this player' in private['footer']['text']
        assert not any(field['name'] == 'Last affected roll' for field in private['fields'])
        await button(panel, 'Active settings').callback(owner_event(1001))
        assert any(field.name == 'Last affected roll' for field in panel.embed().fields)
        assert engine.store.setting(1)['mode'] == 'off'
        await bot.close()
    asyncio.run(check())
