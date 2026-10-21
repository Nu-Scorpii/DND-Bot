import sqlite3
import pytest
from ua_bot.errors import RollError
from ua_bot.wallets import Wallets, MAX_MONEY
from test_avatar import draws


def setup(make_engine, values=(), path=':memory:'):
    engine = make_engine(values, path)
    wallets = Wallets(engine.store, 1)
    wallet = wallets.save(1, 100, 'Avatar', 'coins')['wallet']['id']
    return engine, wallets, wallet


def change(wallets, wallet, value, *, player=42, guild=100, request=None, remove=False, actor=9):
    return wallets.change(actor, guild, wallet, player, value, 'Quest reward' if not remove else 'Shop purchase',
                          is_moderator=True, remove=remove, request_id=request)


def test_add_remove_balances_and_ordered_audit_history(make_engine):
    engine, wallets, wallet = setup(make_engine)
    assert wallets.snapshot(100, wallet, 42)['balance'] == 0
    change(wallets, wallet, 200, request=700)
    result = change(wallets, wallet, 75, remove=True, request=701)
    data = wallets.snapshot(100, wallet, 42)
    assert result['balance'] == data['balance'] == 125
    assert [item['delta'] for item in data['entries']] == [-75, 200]
    assert [item['balance_after'] for item in data['entries']] == [125, 200]
    assert all(item['actor'] == '9' and item['created_at'] > 0 for item in data['entries'])
    assert data['entries'][0]['reason'] == 'Shop purchase' and engine.rng.calls == 0


def test_wallets_are_isolated_by_server_player_and_bot_creator(make_engine):
    engine, wallets, wallet = setup(make_engine)
    other = wallets.save(1, 200, 'Avatar', 'gold')['wallet']['id']
    second = wallets.save(1, 100, 'UA', 'credits')['wallet']['id']
    different_bot = Wallets(engine.store, 2)
    third = different_bot.save(2, 100, 'Avatar')['wallet']['id']
    change(wallets, wallet, 200)
    assert wallets.snapshot(100, wallet, 99)['balance'] == 0
    assert wallets.snapshot(200, other, 42)['balance'] == 0
    assert wallets.snapshot(100, second, 42)['balance'] == 0
    assert different_bot.snapshot(100, third, 42)['balance'] == 0
    with pytest.raises(RollError):
        different_bot.get(100, wallet)
    with pytest.raises(RollError):
        change(wallets, wallet, 100, guild=200)
    with pytest.raises(RollError):
        wallets.save(1, 200, 'Cross-server edit', wallet_id=wallet)
    assert wallets.snapshot(100, wallet, 42)['balance'] == 200


@pytest.mark.parametrize('actor', [1, 42, 9])
@pytest.mark.parametrize('remove', [False, True])
def test_moderator_permission_is_required_even_for_bot_creator(actor, remove, make_engine):
    _, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    with pytest.raises(RollError, match='moderators'):
        wallets.change(actor, 100, wallet, 42, 50, 'Attempt', remove=remove)
    assert wallets.snapshot(100, wallet, 42)['balance'] == 200


def test_only_creator_can_create_rename_or_relabel_wallets(make_engine):
    _, wallets, wallet = setup(make_engine)
    for identity in (9, 42):
        with pytest.raises(RollError, match='creator'):
            wallets.save(identity, 100, 'Other')
        with pytest.raises(RollError, match='creator'):
            wallets.save(identity, 100, 'Other', wallet_id=wallet)
    change(wallets, wallet, 50)
    wallets.save(1, 100, 'Avatar campaign', 'silver', wallet_id=wallet)
    data = wallets.snapshot(100, wallet, 42)
    assert data['wallet']['name'] == 'Avatar campaign' and data['wallet']['currency'] == 'silver'
    assert data['balance'] == 50 and data['count'] == 1


@pytest.mark.parametrize('value', [0, -1, MAX_MONEY + 1, True, False, 1.5, '-5', '1.5', '1,000', '', '1e4', [], {}])
def test_invalid_amount_never_creates_money_or_ledger_rows(value, make_engine):
    _, wallets, wallet = setup(make_engine)
    with pytest.raises(RollError):
        change(wallets, wallet, value)
    data = wallets.snapshot(100, wallet, 42)
    assert data['balance'] == 0 and data['count'] == 0


def test_overdraft_and_overflow_fail_without_partial_changes(make_engine):
    _, wallets, wallet = setup(make_engine)
    with pytest.raises(RollError, match='Not enough'):
        change(wallets, wallet, 1, remove=True)
    change(wallets, wallet, MAX_MONEY)
    with pytest.raises(RollError, match='exceed'):
        change(wallets, wallet, 1)
    change(wallets, wallet, MAX_MONEY, remove=True)
    assert wallets.snapshot(100, wallet, 42)['balance'] == 0
    assert wallets.snapshot(100, wallet, 42)['count'] == 2


@pytest.mark.parametrize('name,currency', [('', 'coins'), ('x' * 33, 'coins'), ('😀' * 17, 'coins'),
                                         ('Avatar\n2', 'coins'), ('Avatar', ''), ('Avatar', 'x' * 21)])
def test_invalid_names_or_labels_do_not_change_existing_wallet(name, currency, make_engine):
    _, wallets, wallet = setup(make_engine)
    with pytest.raises(RollError):
        wallets.save(1, 100, name, currency, wallet_id=wallet)
    assert wallets.get(100, wallet)['name'] == 'Avatar'


def test_wallet_names_are_unique_per_server_and_capacity_keeps_editing_available(make_engine):
    _, wallets, wallet = setup(make_engine)
    with pytest.raises(RollError, match='already exists'):
        wallets.save(1, 100, '  AVATAR  ')
    for index in range(24):
        wallets.save(1, 100, f'Wallet {index}')
    with pytest.raises(RollError, match='25 wallets'):
        wallets.save(1, 100, '26th')
    wallets.save(1, 100, 'Updated', wallet_id=wallet)
    assert len(wallets.list(100)) == 25
    assert len(wallets.list(200)) == 0
    assert wallets.save(1, 200, 'Updated')['wallet']['id'] != wallet


def test_duplicate_money_requests_do_not_repeat_additions_or_removals(make_engine):
    _, wallets, wallet = setup(make_engine)
    first = change(wallets, wallet, 200, request=700)
    change(wallets, wallet, 75, remove=True, request=701)
    repeat = change(wallets, wallet, 200, request=700)
    assert repeat['replayed'] and repeat['entry_id'] == first['entry_id']
    change(wallets, wallet, 75, remove=True, request=701)
    assert wallets.snapshot(100, wallet, 42)['balance'] == 125
    assert wallets.snapshot(100, wallet, 42)['count'] == 2
    with pytest.raises(RollError, match='different change'):
        change(wallets, wallet, 200, player=99, request=700)
    assert wallets.snapshot(100, wallet, 99)['balance'] == 0


def test_duplicate_wallet_create_and_edit_do_not_duplicate_or_undo_newer_names(make_engine):
    engine = make_engine()
    wallets = Wallets(engine.store, 1)
    original = wallets.save(1, 100, 'Avatar', request_id=700)
    wallet = original['wallet']['id']
    wallets.save(1, 100, 'New title', wallet_id=wallet, request_id=701)
    wallets.save(1, 100, 'Latest title', wallet_id=wallet, request_id=702)
    assert wallets.save(1, 100, 'Avatar', request_id=700)['replayed']
    assert wallets.save(1, 100, 'New title', wallet_id=wallet, request_id=701)['replayed']
    assert len(wallets.list(100)) == 1 and wallets.get(100, wallet)['name'] == 'Latest title'


def test_wallet_receipts_persist_independently_of_the_bounded_dice_cache(tmp_path, make_engine):
    path = tmp_path / 'wallets.sqlite3'
    engine, wallets, wallet = setup(make_engine, path=path)
    change(wallets, wallet, 200, request=700)
    for index in range(1005):
        engine.store.remember('roll', index + 1000, 'dice', {'dice': {}})
    restarted = Wallets(make_engine(path=path).store, 1)
    assert change(restarted, wallet, 200, request=700)['replayed']
    assert restarted.snapshot(100, wallet, 42)['balance'] == 200
    assert engine.store.db.execute('SELECT count(*) FROM dice_receipts_v3').fetchone()[0] == 1000


def test_history_pages_are_newest_first_and_do_not_write_balances(make_engine):
    engine, wallets, wallet = setup(make_engine)
    for index in range(12):
        change(wallets, wallet, index + 1)
    first = wallets.snapshot(100, wallet, 42)
    second = wallets.snapshot(100, wallet, 42, page=1)
    last = wallets.snapshot(100, wallet, 42, page=100)
    assert first['pages'] == 3 and last['page'] == 2 and len(last['entries']) == 2
    assert [row['delta'] for row in first['entries'] + second['entries'] + last['entries']] == list(range(12, 0, -1))
    assert engine.store.db.execute('SELECT count(*) FROM currency_balances_v1').fetchone()[0] == 1


@pytest.mark.parametrize('action', ['change', 'create', 'rename'])
def test_failed_receipt_write_rolls_back_the_entire_currency_change(action, make_engine, monkeypatch):
    _, wallets, wallet = setup(make_engine)
    change(wallets, wallet, 200)
    def fail(*args):
        raise sqlite3.OperationalError('simulated failure')
    monkeypatch.setattr(wallets, '_remember', fail)
    with pytest.raises(sqlite3.OperationalError):
        if action == 'change':
            change(wallets, wallet, 75, remove=True, request=700)
        else:
            wallets.save(1, 100, 'Changed', wallet_id=wallet if action == 'rename' else None, request_id=700)
    assert wallets.list(100) == [{'id': wallet, 'name': 'Avatar', 'currency': 'coins'}]
    data = wallets.snapshot(100, wallet, 42)
    assert data['balance'] == 200 and data['count'] == 1


def test_two_connections_apply_updates_without_losing_money(tmp_path, make_engine):
    path = tmp_path / 'concurrent.sqlite3'
    _, first, wallet = setup(make_engine, path=path)
    second = Wallets(make_engine(path=path).store, 1)
    change(first, wallet, 100)
    change(second, wallet, 50)
    change(first, wallet, 25, remove=True)
    assert first.snapshot(100, wallet, 42)['balance'] == second.snapshot(100, wallet, 42)['balance'] == 125


def test_wallet_actions_do_not_consume_avatar_rigs_or_random_draws_and_avatar_keeps_money(make_engine):
    engine, wallets, wallet = setup(make_engine, draws(1))
    engine.configure(1, is_dm=True, mode='force', value=20, target=42, scope='avatar', part='iq', uses=3)
    before = engine.control_status(1, is_dm=True)
    change(wallets, wallet, 200, request=700)
    wallets.snapshot(100, wallet, 42)
    wallets.save(1, 100, 'Avatar campaign', wallet_id=wallet)
    assert engine.rng.calls == 0 and engine.control_status(1, is_dm=True) == before
    char = engine.avatar(42, 100, request_id=700)['character']
    assert char['iq'] == 20 and engine.control_status(1, is_dm=True)['rules'][0]['remaining'] == 2
    assert wallets.snapshot(100, wallet, 42)['balance'] == 200
    assert change(wallets, wallet, 200, request=700)['replayed']
