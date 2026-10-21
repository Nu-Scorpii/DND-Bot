import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from ua_bot.client import DiceBot
from ua_bot.errors import RollError
from ua_bot.storage import Store
from ua_bot.wallets import Wallets, MAX_MONEY, MOD_PERMISSIONS
from test_wallets import setup, change
from test_wallet_ui import event
from test_avatar import draws


def transfer(wallets, wallet, value=75, *, sender=42, receiver=99, guild=100, request=None):
    return wallets.transfer(9, guild, wallet, sender, receiver, value, 'Sword purchase',
                            is_moderator=True, request_id=request)


def totals(wallets, wallet):
    return [wallets.snapshot(100, wallet, player)['balance'] for player in (42, 99)]


def test_transfer_moves_exact_amount_and_records_linked_history_for_both_players(make_engine):
    engine, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    result = transfer(wallets, wallet, request=700)
    assert totals(wallets, wallet) == [125, 75] and sum(totals(wallets, wallet)) == 200
    outgoing = wallets.snapshot(100, wallet, 42)['entries'][0]
    incoming = wallets.snapshot(100, wallet, 99)['entries'][0]
    assert outgoing['transfer_id'] == incoming['transfer_id'] == result['transfer_id']
    assert outgoing['counterparty'] == '99' and incoming['counterparty'] == '42'
    assert [outgoing['delta'], incoming['delta']] == [-75, 75]
    assert outgoing['created_at'] == incoming['created_at']
    assert outgoing['actor'] == incoming['actor'] == '9' and outgoing['reason'] == 'Sword purchase'
    assert engine.rng.calls == 0


@pytest.mark.parametrize('value', [0, -1, 201, MAX_MONEY + 1, True, '1.5', 'one'])
def test_invalid_or_unaffordable_transfer_changes_neither_player(value, make_engine):
    engine, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    with pytest.raises(RollError):
        transfer(wallets, wallet, value)
    assert totals(wallets, wallet) == [200, 0]
    assert engine.store.db.execute('SELECT count(*) FROM currency_transfers_v1').fetchone()[0] == 0
    assert engine.store.db.execute('SELECT count(*) FROM currency_ledger_v1').fetchone()[0] == 1


def test_self_transfer_receiver_limit_and_nonmoderator_fail_before_any_debit(make_engine):
    _, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    change(wallets, wallet, MAX_MONEY, player=99)
    for args in (dict(sender_id=42, receiver_id=42, is_moderator=True),
                 dict(sender_id=42, receiver_id=99, is_moderator=True),
                 dict(sender_id=42, receiver_id=99, is_moderator=False)):
        with pytest.raises(RollError):
            wallets.transfer(1, 100, wallet, value=1, reason='Deal', **args)
    assert totals(wallets, wallet) == [200, MAX_MONEY]


def test_transfers_are_scoped_to_same_server_wallet_and_owner(make_engine):
    engine, wallets, wallet = setup(make_engine)
    other_wallet = wallets.save(1, 100, 'UA')['wallet']['id']
    change(wallets, wallet, 200)
    for guild, selected in ((200, wallet), (100, other_wallet)):
        with pytest.raises(RollError):
            transfer(wallets, selected, guild=guild)
    with pytest.raises(RollError):
        transfer(Wallets(engine.store, 2), wallet)
    assert totals(wallets, wallet) == [200, 0]
    assert wallets.snapshot(100, other_wallet, 99)['balance'] == 0


def test_transfer_replay_survives_restart_and_cannot_move_money_twice(tmp_path, make_engine):
    path = tmp_path / 'transfer.sqlite3'
    _, wallets, wallet = setup(make_engine, path=path)
    change(wallets, wallet, 200)
    original = transfer(wallets, wallet, request=700)
    restarted = Wallets(make_engine(path=path).store, 1)
    result = transfer(restarted, wallet, request=700)
    assert result['replayed'] and result['transfer_id'] == original['transfer_id']
    assert totals(restarted, wallet) == [125, 75]
    with pytest.raises(RollError, match='different change'):
        transfer(restarted, wallet, value=50, request=700)
    assert restarted.snapshot(100, wallet, 99)['count'] == 1


def test_failed_final_write_rolls_back_both_balances_histories_and_transfer_record(make_engine, monkeypatch):
    engine, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    def fail(*args):
        raise sqlite3.OperationalError('simulated failure')
    monkeypatch.setattr(wallets, '_remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        transfer(wallets, wallet, request=700)
    assert totals(wallets, wallet) == [200, 0]
    assert engine.store.db.execute('SELECT count(*) FROM currency_transfers_v1').fetchone()[0] == 0
    assert engine.store.db.execute('SELECT count(*) FROM currency_ledger_v1').fetchone()[0] == 1
    assert engine.store.db.execute('SELECT count(*) FROM currency_requests_v1').fetchone()[0] == 0


def test_simultaneous_transfers_cannot_overspend_the_same_balance(tmp_path, make_engine):
    path = tmp_path / 'concurrent.sqlite3'
    _, wallets, wallet = setup(make_engine, path=path)
    change(wallets, wallet, 100)
    barrier = threading.Barrier(2)
    def pay(receiver):
        store = Store(path)
        try:
            barrier.wait(timeout=5)
            try:
                transfer(Wallets(store, 1), wallet, 80, receiver=receiver, request=receiver + 700)
                return True
            except RollError:
                return False
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pay, receiver) for receiver in (99, 98)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == [False, True]
    assert wallets.snapshot(100, wallet, 42)['balance'] == 20
    assert sum(wallets.snapshot(100, wallet, player)['balance'] for player in (42, 99, 98)) == 100


def test_v35_ledger_migration_keeps_old_money_history_and_receipts(tmp_path, make_engine):
    path = tmp_path / 'legacy.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE currency_wallets_v1(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, guild TEXT,
            name TEXT, name_key TEXT, currency TEXT, UNIQUE(owner,guild,name_key));
        CREATE TABLE currency_balances_v1(wallet INTEGER, player TEXT, balance INTEGER, PRIMARY KEY(wallet,player));
        CREATE TABLE currency_ledger_v1(id INTEGER PRIMARY KEY AUTOINCREMENT, wallet INTEGER, player TEXT,
            actor TEXT, delta INTEGER, balance_after INTEGER, reason TEXT, created_at INTEGER);
        CREATE TABLE currency_requests_v1(owner TEXT, request TEXT, signature TEXT, result TEXT, PRIMARY KEY(owner,request));
        INSERT INTO currency_wallets_v1 VALUES(1,'1','100','Avatar','avatar','coins');
        INSERT INTO currency_balances_v1 VALUES(1,'42',200);
        INSERT INTO currency_ledger_v1 VALUES(1,1,'42','9',200,200,'Quest reward',1234567890);
    ''')
    cached = {'wallet': {'id':1,'name':'Avatar','currency':'coins'}, 'player':'42', 'delta':200,
              'balance':200,'entry_id':1,'replayed':False}
    signature = json.dumps(['money','9','100',1,'42',200,'Quest reward'])
    db.execute('INSERT INTO currency_requests_v1 VALUES(?,?,?,?)', ('1','700',signature,json.dumps(cached)))
    db.commit()
    db.close()
    engine = make_engine(path=path)
    wallets = Wallets(engine.store, 1)
    assert change(wallets, 1, 200, request=700)['replayed']
    assert wallets.snapshot(100, 1, 42)['entries'][0]['transfer_id'] is None
    transfer(wallets, 1, request=701)
    again = Wallets(make_engine(path=path).store, 1)
    assert totals(again, 1) == [125, 75]
    assert again.snapshot(100, 1, 42)['entries'][-1]['created_at'] == 1234567890


def test_transfers_and_avatar_remain_completely_independent(make_engine):
    engine, wallets, wallet = setup(make_engine, draws(1))
    engine.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='iq', uses=3)
    before = engine.control_status(1, is_dm=True)
    change(wallets, wallet, 200)
    transfer(wallets, wallet, request=700)
    assert engine.rng.calls == 0 and engine.control_status(1, is_dm=True) == before
    assert engine.avatar(42, 100, request_id=700)['character']['iq'] == 20
    assert totals(wallets, wallet) == [125, 75]


@pytest.mark.parametrize('action', ['add', 'remove', 'transfer'])
@pytest.mark.parametrize('actor', [1, 42])
def test_money_commands_deny_unprivileged_players_and_creator_before_database_or_member_lookup(action, actor, make_engine):
    async def check():
        bot = DiceBot(make_engine())
        current = event(user=actor)
        player, receiver = SimpleNamespace(id=42), SimpleNamespace(id=99)
        if action == 'transfer':
            await bot.run_wallet_transfer(current, player, receiver, 10)
        else:
            await bot.run_wallet_change(current, player, 10, remove=action == 'remove')
        current.response.defer.assert_not_awaited()
        current.guild.fetch_member.assert_not_awaited()
        assert current.response.send_message.await_args.kwargs['ephemeral']
        assert bot.wallets.list(100) == []
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('permission', MOD_PERMISSIONS)
def test_all_server_moderator_permissions_can_add_money_using_command(permission, make_engine):
    async def check():
        bot = DiceBot(make_engine())
        current = event(user=9, permissions=(permission,))
        current.guild.fetch_member.return_value = SimpleNamespace(id=42, display_name='Hero', bot=False)
        await bot.run_wallet_change(current, SimpleNamespace(id=42), 100)
        wallet = bot.wallets.list(100)[0]['id']
        assert bot.wallets.snapshot(100, wallet, 42)['balance'] == 100
        current.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        assert current.edit_original_response.await_args.kwargs['embed'].title == '✅ Money added'
        await bot.close()
    asyncio.run(check())


def test_mod_transfer_command_moves_money_and_supplies_clear_confirmation(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        wallet = bot.wallets.ensure_default(100)['id']
        bot.wallets.change(9, 100, wallet, 42, 200, 'Reward', is_moderator=True)
        current = event(user=9, permissions=('manage_messages',))
        players = {42:SimpleNamespace(id=42,display_name='Seller *',bot=False),
                   99:SimpleNamespace(id=99,display_name='Buyer @everyone',bot=False)}
        current.guild.fetch_member.side_effect = lambda identity: players[identity]
        await bot.run_wallet_transfer(current, players[42], players[99], 75, reason='Sword purchase')
        assert totals(bot.wallets, wallet) == [125, 75]
        payload = current.edit_original_response.await_args.kwargs
        assert payload['embed'].title == '💸 Transfer complete'
        assert '@everyone' not in str(payload['embed'].to_dict())
        assert payload['allowed_mentions'].everyone is False
        await bot.run_wallet_transfer(current, players[42], players[99], 75, reason='Sword purchase')
        assert totals(bot.wallets, wallet) == [125, 75]
        await bot.close()
    asyncio.run(check())


@pytest.mark.parametrize('case', ['self', 'left', 'bot', 'revoked', 'insufficient', 'foreign_wallet'])
def test_failed_transfer_command_keeps_both_balances_and_completes_private_response(case, make_engine):
    async def check():
        bot = DiceBot(make_engine())
        wallet = bot.wallets.ensure_default(100)['id']
        foreign = bot.wallets.save(1, 200, 'Foreign')['wallet']['id']
        bot.wallets.change(9, 100, wallet, 42, 200, 'Reward', is_moderator=True)
        current = event(user=9, permissions=('manage_messages',))
        sender, receiver = SimpleNamespace(id=42), SimpleNamespace(id=42 if case == 'self' else 99)
        async def fetch(identity):
            if case == 'left':
                raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Gone')
            if case == 'revoked':
                current.user.guild_permissions = discord.Permissions.none()
            return SimpleNamespace(id=identity, display_name='Hero', bot=case == 'bot')
        current.guild.fetch_member.side_effect = fetch
        await bot.run_wallet_transfer(current, sender, receiver, 201 if case == 'insufficient' else 75,
                                      wallet=f'wallet:{foreign}' if case == 'foreign_wallet' else None)
        assert totals(bot.wallets, wallet) == [200, 0]
        assert '⚠️' in current.edit_original_response.await_args.kwargs['content']
        await bot.close()
    asyncio.run(check())


def test_multiple_wallets_require_explicit_choice_for_money_but_not_own_balance(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        first = bot.wallets.save(1, 100, 'Avatar')['wallet']['id']
        second = bot.wallets.save(1, 100, 'UA')['wallet']['id']
        current = event(user=9, permissions=('manage_messages',))
        current.guild.fetch_member.return_value = SimpleNamespace(id=42, display_name='Hero', bot=False)
        await bot.run_wallet_change(current, SimpleNamespace(id=42), 100)
        assert 'several wallets' in current.edit_original_response.await_args.kwargs['content']
        assert bot.wallets.snapshot(100, first, 42)['balance'] == 0
        current = event(1001, user=9, permissions=('manage_messages',))
        current.guild.fetch_member.return_value = SimpleNamespace(id=42, display_name='Hero', bot=False)
        await bot.run_wallet_change(current, SimpleNamespace(id=42), 100, wallet='ua')
        assert bot.wallets.snapshot(100, second, 42)['balance'] == 100
        viewer = event(1002)
        await bot.run_wallet(viewer)
        assert viewer.edit_original_response.await_args.kwargs['view'].wallet_id == first
        viewer.edit_original_response.await_args.kwargs['view'].stop()
        await bot.close()
    asyncio.run(check())


def test_wallet_autocomplete_and_transfer_options_serialize_for_discord(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        local = bot.wallets.save(1, 100, 'Avatar')['wallet']['id']
        bot.wallets.save(1, 200, 'Other server')
        wallet_option = bot.tree.get_command('wallet')._params['wallet']
        choices = await wallet_option.autocomplete(event(), 'ava')
        assert [(c.name,c.value) for c in choices] == [('Avatar',f'wallet:{local}')]
        payload = bot.tree.get_command('wallet-transfer').to_dict(bot.tree)
        assert [option['name'] for option in payload['options']] == ['sender','receiver','amount','wallet','reason']
        assert [option['type'] for option in payload['options'][:3]] == [6,6,4]
        assert payload['options'][3]['autocomplete']
        await bot.close()
    asyncio.run(check())


def test_saved_payment_does_not_invite_double_payment_when_confirmation_fails(make_engine):
    async def check():
        bot = DiceBot(make_engine())
        current = event(user=9, permissions=('manage_messages',))
        current.guild.fetch_member.return_value = SimpleNamespace(id=42, display_name='Hero', bot=False)
        current.edit_original_response.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'No embeds')
        await bot.run_wallet_change(current, SimpleNamespace(id=42), 100)
        wallet = bot.wallets.list(100)[0]['id']
        assert bot.wallets.snapshot(100, wallet, 42)['balance'] == 100
        assert 'was saved' in current.followup.send.await_args.args[0]
        assert current.followup.send.await_args.kwargs['ephemeral']
        await bot.close()
    asyncio.run(check())
