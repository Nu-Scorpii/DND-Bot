"""Server-scoped game currency, separate from character rolls."""

import json
import re
from .errors import require
from .display import length

MAX_WALLETS = 25
MAX_MONEY = 1_000_000_000_000
PAGE_SIZE = 5
MOD_PERMISSIONS = ('administrator', 'manage_guild', 'manage_messages', 'moderate_members', 'kick_members', 'ban_members')


def moderator(interaction):
    """Use actual server permissions, never a role's name or bot ownership."""
    permissions = getattr(interaction.user, 'guild_permissions', None)
    return bool(interaction.guild_id and permissions is not None
                and any(getattr(permissions, permission, False) for permission in MOD_PERMISSIONS))


def snowflake(value):
    require(type(value) is int and 0 < value < 2**64, 'Use this wallet in its Discord server.')
    return str(value)


def text(value, limit, label):
    require(isinstance(value, str) and not any(ord(char) < 32 for char in value), f'Enter a valid {label}.')
    value = ' '.join(value.split())
    require(1 <= length(value) <= limit, f'{label.capitalize()} must be 1–{limit} characters.')
    return value


def amount(value):
    require(isinstance(value, (str, int)) and not isinstance(value, bool) and len(str(value)) <= 30
            and re.fullmatch(r'\d+', str(value).strip()), 'Enter a positive whole amount, such as 100.')
    value = int(str(value).strip())
    require(1 <= value <= MAX_MONEY, 'Choose an amount from 1 to 1 trillion.')
    return value


class Wallets:
    def __init__(self, store, owner_id):
        self.store, self.owner_id = store, owner_id

    def list(self, guild_id):
        return [dict(row) for row in self.store.db.execute(
            'SELECT id,name,currency FROM currency_wallets_v1 WHERE owner=? AND guild=? ORDER BY id',
            (snowflake(self.owner_id), snowflake(guild_id)))]

    def get(self, guild_id, wallet_id):
        require(type(wallet_id) is int and wallet_id > 0, 'Choose a wallet first.')
        row = self.store.db.execute('SELECT id,name,currency FROM currency_wallets_v1 WHERE id=? AND owner=? AND guild=?',
                                    (wallet_id, snowflake(self.owner_id), snowflake(guild_id))).fetchone()
        require(row is not None, 'That wallet is not available in this server. Press Refresh.')
        return dict(row)

    def ensure_default(self, guild_id):
        """A new server can show every player's zero balance immediately."""
        with self.store.transaction():
            wallets = self.list(guild_id)
            if wallets:
                return wallets[0]
            identity = self.store.db.execute(
                'INSERT INTO currency_wallets_v1(owner,guild,name,name_key,currency) VALUES (?,?,?,?,?)',
                (snowflake(self.owner_id), snowflake(guild_id), 'Main', 'main', 'coins')).lastrowid
            return self.get(guild_id, identity)

    def resolve(self, guild_id, selection=None, *, viewing=False):
        wallets = self.list(guild_id)
        if selection is None:
            require(viewing or len(wallets) <= 1, 'This server has several wallets. Choose one in the wallet option.')
            return wallets[0] if wallets else self.ensure_default(guild_id)
        require(isinstance(selection, str) and 1 <= len(selection) <= 100, 'Choose a wallet from the suggestions.')
        if selection.startswith('wallet:'):
            identity = selection.removeprefix('wallet:')
            require(identity.isdecimal() and len(identity) <= 19, 'Choose a wallet from the suggestions.')
            return self.get(guild_id, int(identity))
        key = ' '.join(selection.split()).casefold()
        match = next((wallet for wallet in wallets if wallet['name'].casefold() == key), None)
        require(match is not None, 'That wallet was not found in this server. Choose one from the suggestions.')
        return match

    def snapshot(self, guild_id, wallet_id, player_id, *, page=0, page_size=PAGE_SIZE):
        wallet = self.get(guild_id, wallet_id)
        player = snowflake(player_id)
        require(type(page) is int and page >= 0 and type(page_size) is int and 1 <= page_size <= PAGE_SIZE,
                'Choose a valid history page.')
        # Read balance and history consistently if another bot process writes.
        with self.store.transaction():
            row = self.store.db.execute('SELECT balance FROM currency_balances_v1 WHERE wallet=? AND player=?',
                                       (wallet_id, player)).fetchone()
            count = self.store.db.execute('SELECT count(*) FROM currency_ledger_v1 WHERE wallet=? AND player=?',
                                         (wallet_id, player)).fetchone()[0]
            page = min(page, max(0, (count - 1) // page_size))
            entries = [dict(row) for row in self.store.db.execute(
                'SELECT l.id,l.actor,l.delta,l.balance_after,l.reason,l.created_at,l.transfer_id, '
                'CASE WHEN l.player=t.sender THEN t.receiver ELSE t.sender END AS counterparty '
                'FROM currency_ledger_v1 l LEFT JOIN currency_transfers_v1 t ON t.id=l.transfer_id '
                'WHERE l.wallet=? AND l.player=? ORDER BY l.id DESC LIMIT ? OFFSET ?',
                (wallet_id, player, page_size, page * page_size))]
        return {'wallet': wallet, 'balance': row['balance'] if row else 0, 'entries': entries, 'count': count,
                'page': page, 'pages': max(1, (count + page_size - 1) // page_size)}

    def _receipt(self, request_id, signature):
        if request_id is None:
            return None
        request = snowflake(request_id)
        row = self.store.db.execute('SELECT signature,result FROM currency_requests_v1 WHERE owner=? AND request=?',
                                   (str(self.owner_id), request)).fetchone()
        if row is None:
            return None
        require(row['signature'] == signature, 'This request already saved a different change. Open a new form.')
        return json.loads(row['result'])

    def _remember(self, request_id, signature, result):
        if request_id is not None:
            self.store.db.execute('INSERT INTO currency_requests_v1 VALUES (?,?,?,?)',
                                 (str(self.owner_id), snowflake(request_id), signature, json.dumps(result)))

    def save(self, actor_id, guild_id, name, currency='coins', *, wallet_id=None, request_id=None):
        require(actor_id == self.owner_id, 'Only the bot creator can create or name wallets.')
        actor, guild = snowflake(actor_id), snowflake(guild_id)
        name, currency = text(name, 32, 'wallet name'), text(currency, 20, 'currency label')
        signature = json.dumps(['wallet', actor, guild, wallet_id, name, currency])
        with self.store.transaction():
            cached = self._receipt(request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            if wallet_id is not None:
                self.get(guild_id, wallet_id)
            else:
                require(len(self.list(guild_id)) < MAX_WALLETS, 'This server already has 25 wallets. Edit an existing wallet.')
            duplicate = self.store.db.execute('SELECT id FROM currency_wallets_v1 WHERE owner=? AND guild=? AND name_key=?',
                                             (str(self.owner_id), guild, name.casefold())).fetchone()
            require(duplicate is None or duplicate['id'] == wallet_id, 'A wallet with that name already exists in this server.')
            if wallet_id is None:
                wallet_id = self.store.db.execute(
                    'INSERT INTO currency_wallets_v1(owner,guild,name,name_key,currency) VALUES (?,?,?,?,?)',
                    (str(self.owner_id), guild, name, name.casefold(), currency)).lastrowid
            else:
                self.store.db.execute('UPDATE currency_wallets_v1 SET name=?,name_key=?,currency=? WHERE id=?',
                                      (name, name.casefold(), currency, wallet_id))
            result = {'wallet': self.get(guild_id, wallet_id), 'replayed': False}
            self._remember(request_id, signature, result)
            return result

    def transfer(self, actor_id, guild_id, wallet_id, sender_id, receiver_id, value, reason, *,
                 is_moderator=False, request_id=None):
        require(is_moderator is True, 'Only server moderators can transfer money between players.')
        actor, guild = snowflake(actor_id), snowflake(guild_id)
        sender, receiver = snowflake(sender_id), snowflake(receiver_id)
        require(sender != receiver, 'Choose two different players for a transfer.')
        value, reason = amount(value), text(reason, 120, 'reason')
        signature = json.dumps(['transfer', actor, guild, wallet_id, sender, receiver, value, reason])
        with self.store.transaction():
            cached = self._receipt(request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            wallet = self.get(guild_id, wallet_id)
            balances = {row['player']: row['balance'] for row in self.store.db.execute(
                'SELECT player,balance FROM currency_balances_v1 WHERE wallet=? AND player IN (?,?)',
                (wallet_id, sender, receiver))}
            source, destination = balances.get(sender, 0), balances.get(receiver, 0)
            require(source >= value, f'The sender has only {source:,} {wallet["currency"]}. Nothing was transferred.')
            require(destination + value <= MAX_MONEY, 'The receiver would exceed the balance limit. Nothing was transferred.')
            final_sender, final_receiver = source - value, destination + value
            self.store.db.executemany('INSERT INTO currency_balances_v1 VALUES (?,?,?) '
                                     'ON CONFLICT(wallet,player) DO UPDATE SET balance=excluded.balance',
                                     [(wallet_id, sender, final_sender), (wallet_id, receiver, final_receiver)])
            transfer_id = self.store.db.execute(
                'INSERT INTO currency_transfers_v1(wallet,sender,receiver,actor,amount,reason) VALUES (?,?,?,?,?,?)',
                (wallet_id, sender, receiver, actor, value, reason)).lastrowid
            created_at = self.store.db.execute('SELECT created_at FROM currency_transfers_v1 WHERE id=?',
                                              (transfer_id,)).fetchone()[0]
            self.store.db.executemany(
                'INSERT INTO currency_ledger_v1(wallet,player,actor,delta,balance_after,reason,transfer_id,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                [(wallet_id, sender, actor, -value, final_sender, reason, transfer_id, created_at),
                 (wallet_id, receiver, actor, value, final_receiver, reason, transfer_id, created_at)])
            result = {'wallet': wallet, 'sender': sender, 'receiver': receiver, 'amount': value,
                      'sender_balance': final_sender, 'receiver_balance': final_receiver,
                      'transfer_id': transfer_id, 'replayed': False}
            self._remember(request_id, signature, result)
            return result

    def change(self, actor_id, guild_id, wallet_id, player_id, value, reason, *, remove=False,
               is_moderator=False, request_id=None):
        require(is_moderator is True, 'Only server moderators can add or remove money. Bot ownership does not bypass this.')
        actor, guild, player = snowflake(actor_id), snowflake(guild_id), snowflake(player_id)
        require(type(remove) is bool, 'Choose Add money or Remove money.')
        delta = amount(value) * (-1 if remove else 1)
        reason = text(reason, 120, 'reason')
        signature = json.dumps(['money', actor, guild, wallet_id, player, delta, reason])
        with self.store.transaction():
            cached = self._receipt(request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            wallet = self.get(guild_id, wallet_id)
            row = self.store.db.execute('SELECT balance FROM currency_balances_v1 WHERE wallet=? AND player=?',
                                       (wallet_id, player)).fetchone()
            before = row['balance'] if row else 0
            after = before + delta
            require(after >= 0, f'Not enough money. This balance is {before:,} {wallet["currency"]}.')
            require(after <= MAX_MONEY, 'A balance cannot exceed 1 trillion. Use a smaller amount.')
            self.store.db.execute('INSERT INTO currency_balances_v1 VALUES (?,?,?) '
                                  'ON CONFLICT(wallet,player) DO UPDATE SET balance=excluded.balance',
                                  (wallet_id, player, after))
            entry_id = self.store.db.execute('INSERT INTO currency_ledger_v1(wallet,player,actor,delta,balance_after,reason) '
                                            'VALUES (?,?,?,?,?,?)', (wallet_id, player, actor, delta, after, reason)).lastrowid
            result = {'wallet': wallet, 'player': player, 'delta': delta, 'balance': after, 'entry_id': entry_id, 'replayed': False}
            self._remember(request_id, signature, result)
            return result
