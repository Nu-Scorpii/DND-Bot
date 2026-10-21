"""Small atomic store. Older campaign tables are left untouched and never loaded."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from .controls import empty_controls, empty_setting, MAX_VALUE, MAX_RIGS, MAX_USES, MAX_EXTRA_DICE, EXTRA_MODES, PASSIVE_MODES, PART_DICE, PART_LIMITS, rig_uses, rule_key, is_custom_scope, CUSTOM_FIELD
from .errors import require

CACHE_LIMIT = 1000


class Store:
    def __init__(self, path='data/ua.sqlite3'):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS server_commands_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, guild TEXT NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, draft TEXT NOT NULL, published TEXT,
                revision INTEGER NOT NULL DEFAULT 1, live_revision INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0, actor TEXT NOT NULL,
                UNIQUE(owner,guild,name));
            CREATE TABLE IF NOT EXISTS shop_inventory_v1 (
                command INTEGER NOT NULL, item TEXT NOT NULL, player TEXT NOT NULL,
                name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0 AND quantity <= 1000000),
                PRIMARY KEY(command,item,player));
            CREATE TABLE IF NOT EXISTS dice_settings_v3 (owner TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dice_receipts_v3 (
                kind TEXT NOT NULL, request TEXT NOT NULL, signature TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(kind, request));
            CREATE TABLE IF NOT EXISTS currency_wallets_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL, guild TEXT NOT NULL,
                name TEXT NOT NULL, name_key TEXT NOT NULL, currency TEXT NOT NULL,
                UNIQUE(owner, guild, name_key));
            CREATE TABLE IF NOT EXISTS currency_balances_v1 (
                wallet INTEGER NOT NULL, player TEXT NOT NULL,
                balance INTEGER NOT NULL CHECK(balance >= 0 AND balance <= 1000000000000),
                PRIMARY KEY(wallet, player));
            CREATE TABLE IF NOT EXISTS currency_ledger_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet INTEGER NOT NULL, player TEXT NOT NULL, actor TEXT NOT NULL,
                delta INTEGER NOT NULL, balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)));
            CREATE INDEX IF NOT EXISTS currency_ledger_player_v1
                ON currency_ledger_v1(wallet, player, id DESC);
            CREATE TABLE IF NOT EXISTS currency_requests_v1 (
                owner TEXT NOT NULL, request TEXT NOT NULL,
                signature TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(owner, request));
            CREATE TABLE IF NOT EXISTS currency_transfers_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, wallet INTEGER NOT NULL,
                sender TEXT NOT NULL, receiver TEXT NOT NULL, actor TEXT NOT NULL,
                amount INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)));
        ''')
        # Preserve existing wallet data; serialise migration across connections.
        with self.transaction():
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(currency_ledger_v1)')}
            if 'transfer_id' not in columns:
                self.db.execute('ALTER TABLE currency_ledger_v1 ADD COLUMN transfer_id INTEGER')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    @staticmethod
    def _rule(state, identity):
        mode, value, target = state['mode'], state['value'], state['target']
        part = state.get('part')
        scope = state.get('scope', 'avatar' if part else 'all')
        assert mode in ('good', 'bad', 'force', 'shift', 'face', *EXTRA_MODES)
        custom = is_custom_scope(scope)
        assert scope in ('roll', 'avatar', 'all') or custom
        assert part is None or isinstance(part, str) and (bool(CUSTOM_FIELD.fullmatch(part)) if custom else part in PART_DICE and mode != 'face')
        assert scope != 'roll' or part is None
        assert scope != 'avatar' or part is not None or mode in PASSIVE_MODES
        assert not custom or part is not None or mode in PASSIVE_MODES
        assert target is None or isinstance(target, str) and target.isdecimal() and 0 < int(target) < 2**64
        if mode in ('force', 'shift'):
            assert type(value) is int and abs(value) <= MAX_VALUE
            if part is not None and mode == 'force' and not custom:
                assert PART_LIMITS[part][0] <= value <= PART_LIMITS[part][1]
        elif mode in EXTRA_MODES:
            assert type(value) is int and 1 <= value <= MAX_EXTRA_DICE
        elif mode == 'face':
            assert isinstance(value, str) and 1 <= len(value) <= 40 and not any(ord(c) < 32 for c in value)
        else:
            value = 100 if value is None else value
            assert type(value) is int and 1 <= value <= 100
        assert type(identity) is int and identity > 0
        remaining = state.get('remaining', rig_uses(mode))
        assert remaining is None or type(remaining) is int and 1 <= remaining <= MAX_USES
        return dict(id=identity, mode=mode, value=value, target=target, part=part, scope=scope, remaining=remaining)

    def controls(self, owner):
        """Read stacked controls, upgrading a v3 single setting without losing it."""
        row = self.db.execute('SELECT state FROM dice_settings_v3 WHERE owner=?', (str(owner),)).fetchone()
        if not row:
            return empty_controls()
        try:
            state = json.loads(row['state'])
            assert isinstance(state, dict)
            last = state.get('last')
            assert last is None or isinstance(last, str) and len(last) <= 1000
            if 'rules' not in state:
                rules = [] if state['mode'] == 'off' else [self._rule(state, 1)]
                return {'rules': rules, 'next_id': 2, 'last': last}
            assert isinstance(state['rules'], list) and len(state['rules']) <= MAX_RIGS
            rules, identities, keys = [], set(), set()
            for item in state['rules']:
                try:
                    rule = self._rule(item, item['id'])
                    assert rule['id'] not in identities and rule_key(rule) not in keys
                except (ValueError, TypeError, KeyError, AssertionError):
                    # One broken entry must not erase unrelated valid controls.
                    continue
                identities.add(rule['id'])
                keys.add(rule_key(rule))
                rules.append(rule)
            next_id = state.get('next_id', 1)
            assert type(next_id) is int and next_id > 0
            return {'rules': rules, 'next_id': max(next_id, max(identities, default=0) + 1), 'last': last}
        except (ValueError, TypeError, KeyError, AssertionError):
            return empty_controls()

    def save_controls(self, owner, controls):
        self.db.execute('INSERT INTO dice_settings_v3 VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET state=excluded.state',
                        (str(owner), json.dumps(controls, ensure_ascii=False)))

    def setting(self, owner):
        """Compatibility snapshot for the internal pre-panel command adapter."""
        controls = self.controls(owner)
        last = controls['rules'][-1] if controls['rules'] else empty_setting()
        return {key: last.get(key) if key != 'last' else controls['last'] for key in empty_setting()}

    def save_setting(self, owner, setting):
        self.db.execute('INSERT INTO dice_settings_v3 VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET state=excluded.state',
                        (str(owner), json.dumps(setting, ensure_ascii=False)))

    def receipt(self, kind, request, signature):
        if request is None:
            return None
        row = self.db.execute('SELECT signature,result FROM dice_receipts_v3 WHERE kind=? AND request=?', (kind, str(request))).fetchone()
        if not row:
            return None
        require(row['signature'] == signature, 'This request ID was already used for a different command. Run the command again.')
        return json.loads(row['result'])

    def remember(self, kind, request, signature, result):
        if request is None:
            return
        self.db.execute('INSERT INTO dice_receipts_v3 VALUES (?,?,?,?)',
                        (kind, str(request), signature, json.dumps(result, ensure_ascii=False)))
        self.db.execute('DELETE FROM dice_receipts_v3 WHERE rowid NOT IN (SELECT rowid FROM dice_receipts_v3 ORDER BY rowid DESC LIMIT ?)', (CACHE_LIMIT,))

    def close(self):
        self.db.close()
