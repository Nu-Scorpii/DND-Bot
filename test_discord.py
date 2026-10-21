import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.dice import prepare
from ua_bot.display import length, roll_messages
from ua_bot.errors import RollError
from conftest import ScriptedRNG


def interaction(request=900):
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock(), defer=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(id=request, guild_id=100, user=SimpleNamespace(id=2, display_name='Hero'),
                           response=response, followup=followup, extras={}, edit_original_response=AsyncMock())


def test_nine_slash_commands_and_minimal_intents(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        assert [command.name for command in bot.tree.get_commands()] == ['roll', 'avatar', 'wallet', 'wallet-add', 'wallet-remove', 'wallet-transfer', 'wallet-manage', 'builder', 'custom']
        payload = bot.tree.get_commands()[0].to_dict(bot.tree)
        assert [option['name'] for option in payload['options']] == ['dice']
        assert not payload['options'][0]['required']
        assert bot.tree.get_commands()[1].to_dict(bot.tree).get('options', []) == []
        assert not bot.intents.message_content
        assert not bot.intents.guild_messages
        assert bot.intents.dm_messages
        assert bot.dispatch.__func__ is discord.Client.dispatch
        await bot.close()
        await bot.close()
    asyncio.run(check())


def test_global_sync_and_old_guild_commands_removed_once(make_engine, monkeypatch):
    async def check():
        bot = DiceBot(make_engine())
        synced = AsyncMock(return_value=bot.tree.get_commands())
        monkeypatch.setattr(bot.tree, 'sync', synced)
        await bot.setup_hook()
        synced.assert_awaited_once_with()
        guild = discord.Object(id=100)
        bot.tree.copy_global_to(guild=guild)
        assert bot.tree.get_commands(guild=guild)
        await bot.clean_guild_commands(guild)
        assert bot.tree.get_commands(guild=guild) == []
        await bot.clean_guild_commands(guild)
        assert synced.await_count == 2
        await bot.close()
    asyncio.run(check())


def test_roll_message_is_simple_and_shows_bonus():
    result = prepare('1d20', bonus=3).roll(ScriptedRNG([8]))
    assert roll_messages(result, 'Hero') == ['🎲 **Hero rolled 1d20**\n\nRolls: `8`\nBonus: `+3`\n\n**Total: 11**']


def test_long_text_dice_are_chunked_and_mentions_and_markdown_escaped():
    face = '@everyone `**' + '😀' * 20
    result = prepare('100d[custom]', faces=[face]).roll(ScriptedRNG([face] * 100))
    parts = roll_messages(result, '@everyone **Hero**')
    assert len(parts) > 1
    assert all(length(part) <= 2000 for part in parts)
    assert all('@everyone' not in part for part in parts)
    assert sum(part.count('😀') for part in parts) == 2000
    assert '**Total:' not in ''.join(parts)


def test_roll_delivery_excludes_private_state_and_notices(make_engine):
    async def check():
        engine = make_engine([4])
        engine.private_message(1, '!Rigger 100', is_dm=True)
        bot = DiceBot(engine)
        bot.notify_creator = AsyncMock()
        event = interaction()
        await bot.run_roll(event, dice='1d6')
        event.response.defer.assert_awaited_once_with(thinking=True)
        public = event.edit_original_response.await_args.kwargs['content']
        assert '**Total: 4**' in public
        assert 'Rigger' not in public and 'force' not in public and '100' not in public
        assert not event.followup.send.await_args_list
        bot.notify_creator.assert_awaited_once()
        await bot.close()
    asyncio.run(check())


def test_invalid_input_gets_private_reply_without_public_defer(make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        event = interaction()
        await bot.run_roll(event, dice='0d20')
        event.response.defer.assert_not_awaited()
        assert event.response.send_message.await_args.kwargs['ephemeral'] is True
        assert engine.rng.calls == 0
        await bot.close()
    asyncio.run(check())


def test_public_defer_error_details_stay_private(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        event = interaction()
        event.response.is_done.return_value = True
        event.extras['roll_deferred'] = True
        await bot.command_error(event, RollError('private error detail'))
        assert 'private error detail' not in event.edit_original_response.await_args.kwargs['content']
        assert 'private error detail' in event.followup.send.await_args.args[0]
        assert event.followup.send.await_args.kwargs['ephemeral'] is True
        await bot.close()
    asyncio.run(check())


def test_only_creator_dm_receives_command_reply(make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        channel = MagicMock(spec=discord.DMChannel)
        channel.send = AsyncMock()
        event = SimpleNamespace(id=800, author=SimpleNamespace(id=2, bot=False), content='!Rigger 15', channel=channel)
        await bot.on_message(event)
        channel.send.assert_not_awaited()
        event.author.id = 1
        await bot.on_message(event)
        channel.send.assert_awaited_once()
        assert channel.send.await_args.kwargs['view'] is bot.rigger_panel
        assert channel.send.await_args.kwargs['embed'].title == '🔒 Control'
        assert engine.store.setting(1)['mode'] == 'off'
        await bot.close()
    asyncio.run(check())


def test_unavailable_creator_dm_does_not_undo_roll(make_engine, monkeypatch):
    async def check():
        engine = make_engine([4])
        engine.private_message(1, '!Rigger 100', is_dm=True)
        bot = DiceBot(engine)
        monkeypatch.setattr(bot, 'get_user', lambda _: SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'DM blocked'))))
        event = interaction()
        await bot.run_roll(event, dice='1d6')
        assert '**Total: 4**' in event.edit_original_response.await_args.kwargs['content']
        assert engine.store.setting(1)['mode'] == 'off'
        assert 'could not apply' in engine.private_message(1, '!Rigger status', is_dm=True)
        await bot.close()
    asyncio.run(check())


def test_slash_in_dm_cannot_consume_private_setting(make_engine):
    async def check():
        engine = make_engine()
        engine.private_message(1, '!Rigger 15', is_dm=True)
        bot = DiceBot(engine)
        event = interaction()
        event.guild_id = None
        await bot.run_roll(event)
        assert engine.store.setting(1)['mode'] == 'force'
        assert engine.rng.calls == 0
        assert event.response.send_message.await_args.kwargs['ephemeral'] is True
        await bot.close()
    asyncio.run(check())
