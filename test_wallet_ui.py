import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.errors import RollError
from ua_bot.wallet_ui import WalletPanel, WalletInput, WalletChoice
from ua_bot.wallets import MOD_PERMISSIONS, moderator
from test_rigger_panel import button, owner_event


def event(request=1000, user=42, guild=100, permissions=()):
    value = owner_event(request, owner=user, guild=guild)
    value.user.display_name = f'Player {user}'
    value.user.guild_permissions = discord.Permissions(**{name: True for name in permissions})
    value.user.bot = False
    value.guild = SimpleNamespace(id=guild, name='Avatar guild', fetch_member=AsyncMock()) if guild else None
    value.edit_original_response.return_value = SimpleNamespace(edit=AsyncMock())
    async def defer(**kwargs):
        value.response.is_done.return_value = True
    value.response.defer.side_effect = defer
    return value


def panel_for(bot, value, **kwargs):
    panel = WalletPanel(bot, value, **kwargs)
    panel.message = SimpleNamespace(edit=AsyncMock())
    return panel


@pytest.mark.parametrize('permission', MOD_PERMISSIONS)
def test_real_server_moderator_permissions_are_recognised(permission):
    assert moderator(event(permissions=(permission,)))
    assert not moderator(event(permissions=('manage_roles',)))
    assert not moderator(event(guild=None, permissions=(permission,)))


@pytest.mark.parametrize('viewer,permissions', [(42, ()), (1, ()), (9, ('administrator',))])
def test_wallet_is_available_to_everyone_and_only_shows_their_own_balance(viewer, permissions, make_engine):
    async def check():
        engine = make_engine()
        engine.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='iq', uses=3)
        before = engine.control_status(1, is_dm=True)
        bot = DiceBot(engine)
        current = event(user=viewer, permissions=permissions)
        await bot.run_wallet(current)
        current.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        payload = current.edit_original_response.await_args.kwargs
        assert payload['embed'].title == '🪙 Main' and '**0 coins**' in payload['embed'].description
        panel = payload['view']
        assert panel.author_id == viewer and not panel.manage
        assert [getattr(item, 'label', None) for item in panel.children if isinstance(item, discord.ui.Button)] == ['History', 'Refresh']
        assert len(bot.wallets.list(100)) == 1
        assert engine.control_status(1, is_dm=True) == before and engine.rng.calls == 0
        command = bot.tree.get_command('wallet').to_dict(bot.tree)
        assert command['default_member_permissions'] is None and command['contexts'] == [0]
        assert command['options'][0]['autocomplete']
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_wallet_in_dm_is_private_error(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        current = event(guild=None)
        await bot.run_wallet(current)
        current.response.defer.assert_not_awaited()
        assert current.response.send_message.await_args.kwargs['ephemeral']
        await bot.close()
    asyncio.run(check())


def test_creator_names_wallets_in_separate_manage_panel_and_keeps_money(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = panel_for(bot, event(user=1), manage=True)
        wallet = panel.wallet_id
        bot.wallets.change(9, 100, wallet, 1, 50, 'Reward', is_moderator=True)
        modal = WalletInput(panel, 'edit')
        modal.first._value, modal.second._value = 'Avatar', 'gold'
        await modal.on_submit(event(1001, user=1))
        assert bot.wallets.snapshot(100, wallet, 1)['balance'] == 50
        assert panel.embed().title == '🪙 Avatar'
        new = WalletInput(panel, 'new')
        new.first._value, new.second._value = 'UA', 'credits'
        await new.on_submit(event(1002, user=1))
        assert len(bot.wallets.list(100)) == 2
        for kind in ('add', 'remove', 'transfer'):
            with pytest.raises(RollError):
                WalletInput(panel, kind)
        modal.stop()
        new.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('case', ['noncreator', 'other_viewer', 'other_server', 'expired', 'changed_wallet', 'reader_panel'])
def test_wallet_naming_forms_require_creator_and_valid_current_panel(case, make_engine):
    async def check():
        bot = DiceBot(make_engine())
        panel = panel_for(bot, event(user=1), manage=case != 'reader_panel')
        wallet = panel.wallet_id
        second = bot.wallets.save(1, 100, 'Other')['wallet']['id']
        modal = WalletInput(panel, 'edit')
        modal.first._value, modal.second._value = 'Forged', 'gold'
        current = event(1001, user=1)
        if case in ('noncreator', 'other_viewer'):
            current.user.id = 9
        elif case == 'other_server':
            current.guild_id = 200
        elif case == 'expired':
            panel.stop()
        elif case == 'changed_wallet':
            panel.wallet_id = second
        await modal.on_submit(current)
        assert bot.wallets.get(100, wallet)['name'] == 'Main'
        reply = current.response.send_message.await_args or current.followup.send.await_args
        assert reply.kwargs['ephemeral']
        modal.stop()
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_only_creator_can_open_manage_command(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        current = event(user=9, permissions=('administrator',))
        await bot.run_wallet(current, manage=True)
        current.response.defer.assert_not_awaited()
        assert bot.wallets.list(100) == []
        await bot.close()
    asyncio.run(check())


def test_history_shows_both_sides_of_a_deal_and_pages_correctly(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        wallet = bot.wallets.ensure_default(100)['id']
        bot.wallets.change(9, 100, wallet, 42, 200, 'Reward', is_moderator=True)
        for index in range(6):
            bot.wallets.transfer(9, 100, wallet, 42, 99, 10, 'Deal', is_moderator=True)
        sender = panel_for(bot, event())
        receiver = panel_for(bot, event(user=99))
        assert '**140 coins**' in sender.embed().description
        assert 'Sent to player 99' in str(sender.embed().to_dict())
        assert 'Received from player 42' in str(receiver.embed().to_dict())
        await button(sender, 'History').callback(event(1001))
        assert 'Page 1/2' in sender.embed().footer.text
        await button(sender, 'Next').callback(event(1002))
        assert 'Page 2/2' in sender.embed().footer.text
        sender.stop()
        receiver.stop()
        await bot.close()
    asyncio.run(check())


def test_wallet_selector_rejects_other_servers_and_other_viewers(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        foreign = bot.wallets.save(1, 200, 'Foreign')['wallet']['id']
        panel = panel_for(bot, event())
        local = panel.wallet_id
        choice = next(item for item in panel.children if isinstance(item, WalletChoice))
        choice._values = [str(foreign)]
        await choice.callback(event(1001))
        assert panel.wallet_id == local
        choice._values = [str(local)]
        current = event(1002, user=99)
        await choice.callback(current)
        assert current.response.send_message.await_args.kwargs['ephemeral']
        panel.stop()
        await bot.close()
    asyncio.run(check())


def test_real_wallet_and_creator_payloads_fit_discord_limits(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        for index in range(25):
            bot.wallets.save(1, 100, f'{index} ' + '*' * 28, '`' * 20)
        panel = panel_for(bot, event(user=1), manage=True)
        panel.player.display_name = '@everyone **' + '😀' * 100
        panel.guild_name = '@everyone **Server**'
        bot.wallets.change(9, 100, panel.wallet_id, 1, 1000, '@everyone ' + '*' * 110, is_moderator=True)
        for index in range(7):
            bot.wallets.transfer(9, 100, panel.wallet_id, 1, 42, 100, '@everyone ' + '*' * 110, is_moderator=True)
        panel.history = True
        panel.build_items()
        embed = panel.embed()
        assert len(embed) < 6000 and '@everyone' not in str(embed.to_dict())
        assert all(len(field.value) <= 1024 and len(field.name) <= 256 for field in embed.fields)
        rows = panel.to_components()
        assert len(rows) <= 5 and all(len(row['components']) <= 5 for row in rows)
        assert len(next(item for item in panel.children if isinstance(item, WalletChoice)).options) == 25
        for kind in ('new', 'edit'):
            modal = WalletInput(panel, kind)
            assert len(modal.to_dict()['components']) == 2
            modal.stop()
        await panel.on_timeout()
        assert all(item.disabled for item in panel.children)
        panel.stop()
        await bot.close()
    asyncio.run(check())
