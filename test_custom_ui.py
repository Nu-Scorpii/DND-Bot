import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from ua_bot.builder_ui import BuilderInput, BuilderPanel
from ua_bot.client import DiceBot
from ua_bot.custom import allowed, manager
from ua_bot.custom_ui import Catalogue, ContentPanel, Checkout, QuantityInput, character_embeds
from ua_bot.display import length
from ua_bot.rigger_ui import RiggerPanel
from test_custom_commands import command, die, table, rig, money
from test_rigger_panel import button, select, owner_event
from test_wallet_ui import event


def current(request=1000, user=9, guild=100, permissions=('manage_guild',), owner=9):
    value = event(request, user=user, guild=guild, permissions=permissions)
    if value.guild:
        value.guild.owner_id = owner
        value.guild.fetch_member.return_value = SimpleNamespace(id=user, bot=False)
    value.original_response = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
    return value


async def submit(modal, values, interaction):
    for key, value in values.items(): modal.fields[key]._value = value
    await modal.on_submit(interaction)


def payload_limits(panel):
    payload = panel.to_components()
    assert len(payload) <= 5
    for row in payload:
        assert len(row['components']) <= 5
        for item in row['components']:
            if 'options' in item: assert 1 <= len(item['options']) <= 25
    embed = panel.embed().to_dict()
    assert length(embed.get('title', '')) <= 256
    assert length(embed.get('description', '')) <= 4096
    for field in embed.get('fields', []):
        assert 1 <= length(field['name']) <= 256
        assert 1 <= length(field['value']) <= 1024
    total = length(embed.get('title', '') + embed.get('description', '') + embed.get('footer', {}).get('text', ''))
    total += sum(length(f['name'] + f['value']) for f in embed.get('fields', []))
    assert total <= 6000


@pytest.mark.parametrize('user,owner,permissions,rolls,faq', [
    (9, 9, (), True, True), (9, 8, ('administrator',), True, True),
    (9, 8, ('manage_guild',), True, True), (9, 8, ('moderate_members',), False, True),
    (1, 8, (), False, False), (42, 8, (), False, False)])
def test_server_permissions_do_not_grant_builder_to_bot_owner(user, owner, permissions, rolls, faq):
    value = current(user=user, owner=owner, permissions=permissions)
    assert manager(value) == rolls
    assert allowed(value, 'roll') == rolls and allowed(value, 'faq') == faq


def test_builder_full_modal_workflow_and_private_preview_do_not_spend_rigs(make_engine):
    async def check():
        engine = make_engine([1, 1, 2, 3, 4, 5, 6, 19])
        bot = DiceBot(engine); bot.sync_custom_guild = AsyncMock()
        first = current()
        await bot.run_builder(first)
        panel = first.edit_original_response.await_args.kwargs['view']
        assert isinstance(panel, BuilderPanel)
        first.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        await button(panel, 'New command').callback(current(1001))
        modal = BuilderInput(panel, 'new', command_kind='roll')
        await submit(modal, dict(slug='hero', title='Heroes', description='Make your hero.'), current(1002))
        assert panel.command['name'] == 'hero' and panel.command['published'] is None
        for index, (name, expression) in enumerate([('Origin', '1d5'), ('Stats', '6d20'), ('Power', '1d20')]):
            form = BuilderInput(panel, 'entry', entry_type='dice')
            await submit(form, dict(name=name, dice=expression, when=''), current(1010 + index))
        assert len(panel.command['draft']['entries']) == 3
        payload_limits(panel)
        await button(panel, 'Publish').callback(current(1020))
        bot.sync_custom_guild.assert_awaited_once()
        assert panel.command['published']['entries'] == panel.command['draft']['entries']
        rig(engine, panel.command, 'good', 100, target=9)
        preview = current(1021)
        await button(panel, 'Preview').callback(preview)
        preview.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        assert engine.rng.calls == 0 and engine.store.controls(1)['rules'][0]['remaining'] == 1
        output = current(1022)
        await bot.run_custom(output, selection='/hero')
        output.response.defer.assert_awaited_once_with(thinking=True)
        embed = output.edit_original_response.await_args.kwargs['embed']
        assert [f.name for f in embed.fields] == ['Origin', 'Stats', 'Power']
        assert '**Total: 120**' in embed.fields[1].value
        assert not engine.store.controls(1)['rules']
        assert all(word not in str(embed.to_dict()) for word in ('rigger', 'good luck', 'remaining', 'custom:'))
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_builder_stale_forms_revoked_permissions_and_other_players_cannot_save(make_engine):
    async def check():
        bot = DiceBot(make_engine()); cmd = command(bot.engine, publish=False)
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(cmd['id']))
        form = BuilderInput(panel, 'edit')
        doc = copy.deepcopy(cmd['draft']); doc['title'] = 'Newer draft'
        newer = bot.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
        interaction = current(1100)
        await submit(form, dict(name='Old draft', dice='1d100', when=''), interaction)
        assert bot.customs.get(100, cmd['id']) == newer
        assert 'changed elsewhere' in interaction.followup.send.await_args.args[0]
        for rejected in [current(1101, user=8), current(1102, guild=200), current(1103, permissions=(), owner=8)]:
            await button(panel, 'Remove entry').callback(rejected)
            assert rejected.response.send_message.await_args.kwargs['ephemeral']
            assert bot.customs.get(100, cmd['id']) == newer
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_mod_can_build_shop_or_faq_but_not_character_command(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        mod = current(permissions=('moderate_members',), owner=8)
        panel = BuilderPanel(bot, mod)
        await button(panel, 'New command').callback(mod)
        assert {o.value for o in panel.children[0].options} == {'faq', 'shop'}
        fake = BuilderInput(panel, 'new', command_kind='roll')
        await submit(fake, dict(slug='nope', title='Nope', description=''), mod)
        assert bot.customs.list(100) == []
        shop = BuilderInput(panel, 'new', command_kind='shop')
        await submit(shop, dict(slug='market', title='Market', description='Buy supplies'), current(1201, permissions=('moderate_members',), owner=8))
        assert panel.command['kind'] == 'shop'
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_dynamic_commands_restore_without_shadowing_avatar_and_remove_on_unpublish(make_engine):
    async def check():
        bot = DiceBot(make_engine([8])); hero = command(bot.engine); command(bot.engine, kind='faq', name='rules')
        command(bot.engine, guild=200, name='other')
        bot.tree.sync = AsyncMock(return_value=[])
        guild = discord.Object(id=100)
        await bot.clean_guild_commands(guild)
        assert {c.name for c in bot.tree.get_commands(guild=guild)} == {'hero', 'rules'}
        assert bot.tree.get_command('avatar') is not None and len(bot.tree.get_commands()) == 10
        dynamic = bot.tree.get_command('hero', guild=guild)
        assert dynamic.to_dict(bot.tree)['options'] == []
        response = current(1300, user=42, permissions=())
        await dynamic.callback(response)
        assert '**Total: 8**' in response.edit_original_response.await_args.kwargs['embed'].fields[0].value
        bot.customs.visibility(9, 100, hero['id'], hero['revision'], 'unpublish', is_manager=True)
        await bot.sync_custom_guild(guild, force=True)
        assert {c.name for c in bot.tree.get_commands(guild=guild)} == {'rules'}
        await bot.close()
    asyncio.run(check())


def test_slash_sync_failure_keeps_published_data_accessible_via_custom(make_engine):
    async def check():
        bot = DiceBot(make_engine([7])); cmd = command(bot.engine, publish=False)
        bot.tree.sync = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason='Server error'), 'unavailable'))
        panel = BuilderPanel(bot, current()); await panel.choose_command(current(), str(cmd['id']))
        await button(panel, 'Publish').callback(current(1400))
        assert 'sync is pending' in panel.note and 100 not in bot.cleaned_guilds
        output = current(1401, user=42, permissions=())
        await bot.run_custom(output, selection='hero')
        assert '**Total: 7**' in output.edit_original_response.await_args.kwargs['embed'].fields[0].value
        bot.tree.sync = AsyncMock(return_value=[])
        await bot.clean_guild_commands(discord.Object(id=100))
        assert 100 in bot.cleaned_guilds
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_rigger_custom_picker_named_outcomes_text_faces_and_active_tracking(make_engine):
    async def check():
        engine = make_engine([1]); entry = table(source='Rare | 1\nCommon | 3')
        cmd = command(engine, [entry, die('Element', '1d["sun","rain"]')])
        bot = DiceBot(engine); panel = RiggerPanel(bot)
        panel.message = SimpleNamespace(edit=AsyncMock())
        await button(panel, 'Custom rolls').callback(owner_event(1500))
        picker = select(panel, 'custom'); picker._values = [f"custom:{cmd['id']}"]
        await picker.callback(owner_event(1501))
        picker = select(panel, 'roll'); picker._values = [entry['id']]
        await picker.callback(owner_event(1502))
        picker = select(panel, 'mode'); picker._values = ['force']
        await picker.callback(owner_event(1503))
        picker = select(panel, 'outcome')
        assert [(o.label, o.value) for o in picker.options] == [('Rare', '1'), ('Common', '2')]
        picker._values = ['2']; await picker.callback(owner_event(1504))
        await button(panel, 'Apply').callback(owner_event(1505))
        assert engine.store.controls(1)['rules'][0]['value'] == 2
        await button(panel, 'Active settings').callback(owner_event(1506))
        assert '/hero · Nation' in str(panel.embed().to_dict())
        payload_limits(panel)
        await button(panel, 'Back to draft').callback(owner_event(1507))
        picker = select(panel, 'roll'); picker._values = [cmd['draft']['entries'][1]['id']]
        await picker.callback(owner_event(1508))
        assert panel.draft['mode'] == 'face' and panel.allowed_modes() == ('face', 'extra_high', 'extra_low')
        payload_limits(panel)
        await button(panel, 'Standard rolls').callback(owner_event(1509))
        assert 'bending_strength' in panel.roll_options()
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_shop_ui_reviews_quantity_before_single_charge_and_records_inventory(make_engine):
    async def check():
        bot = DiceBot(make_engine()); cmd = command(bot.engine, kind='shop'); wallet = money(bot.engine, cmd)
        player = current(1600, user=42, permissions=())
        await bot.run_custom(player, identity=cmd['id'])
        player.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        panel = player.edit_original_response.await_args.kwargs['view']; payload_limits(panel)
        modal = QuantityInput(panel); modal.quantity._value = '2'
        form = current(1601, user=42, permissions=())
        await modal.on_submit(form)
        quote = form.response.send_message.await_args.kwargs['view']
        assert isinstance(quote, Checkout) and bot.wallets.snapshot(100, wallet, 42)['balance'] == 100
        payload_limits(quote)
        confirm = current(1602, user=42, permissions=())
        await button(quote, 'Confirm purchase').callback(confirm)
        await button(quote, 'Confirm purchase').callback(current(1603, user=42, permissions=()))
        assert bot.wallets.snapshot(100, wallet, 42)['balance'] == 50
        await button(panel, 'My items').callback(current(1604, user=42, permissions=()))
        assert panel.embed().fields[0].value == 'Owned: **2**'
        assert quote.is_finished()
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_stale_shop_modal_and_cancel_do_not_debit(make_engine):
    async def check():
        bot = DiceBot(make_engine()); cmd = command(bot.engine, kind='shop'); wallet = money(bot.engine, cmd)
        player = current(1700, user=42, permissions=())
        panel = ContentPanel(bot, player, cmd); modal = QuantityInput(panel); modal.quantity._value = '1'
        bot.customs.visibility(9, 100, cmd['id'], cmd['revision'], 'publish', is_moderator=True)
        await modal.on_submit(player)
        assert 'shop changed' in player.response.send_message.await_args.args[0]
        quote = Checkout(bot, current(1701, user=42, permissions=()), cmd, cmd['draft']['entries'][0]['id'], 1)
        await button(quote, 'Cancel').callback(current(1702, user=42, permissions=()))
        assert quote.is_finished() and bot.wallets.snapshot(100, wallet, 42)['balance'] == 100
        panel.stop(); await bot.close()
    asyncio.run(check())


def test_large_unicode_content_stays_within_discord_limits(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        entries = [dict(id='field_000000000001', name='😀' * 25, answer='😀_*' * 375)]
        # Only one question here; test worst-case escaped UTF-16 length, not codepoints.
        cmd = command(bot.engine, entries, kind='faq', name='rules')
        doc = copy.deepcopy(cmd['draft']); doc['description'] = '😀_*' * 175
        cmd = bot.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_moderator=True)
        cmd = bot.customs.visibility(9, 100, cmd['id'], cmd['revision'], 'publish', is_moderator=True)
        player = current(1800, user=42, permissions=())
        panels = [ContentPanel(bot, player, cmd), Catalogue(bot, player), BuilderPanel(bot, current())]
        await panels[-1].choose_command(current(), str(cmd['id']))
        for panel in panels: payload_limits(panel); panel.stop()
        await bot.close()
    asyncio.run(check())
