"""Server command definitions, weighted character steps, and atomic shop purchases."""

import json
import math
import re
import secrets
from fractions import Fraction
from functools import reduce

from .dice import prepare, values_of
from .display import length
from .errors import RollError, require
from .wallets import MAX_MONEY, Wallets, moderator, snowflake, text

MAX_COMMANDS = 20
MAX_ENTRIES = 20
MAX_TEMPLATE_DICE = 200
KINDS = ('roll', 'faq', 'shop')
OUTCOME_TYPES = ('table', 'probability')
RESERVED = {'roll', 'calculate', 'avatar', 'wallet', 'wallet-add', 'wallet-remove', 'wallet-transfer',
            'wallet-manage', 'builder', 'custom', 'rigger', 'dice'}
SLUG = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')
FIELD = re.compile(r'field_[a-f0-9]{12}\Z')
OPS = ('==', '!=', '>=', '<=', '>', '<')
THEMES = {'roll': 0x8B5CF6, 'faq': 0x38BDF8, 'shop': 0xF5B942}


def manager(interaction):
    guild = getattr(interaction, 'guild', None)
    if guild is None or guild.id != interaction.guild_id:
        return False
    perms = getattr(interaction.user, 'guild_permissions', None)
    return bool(getattr(guild, 'owner_id', None) == interaction.user.id or perms is not None and
                (getattr(perms, 'administrator', False) or getattr(perms, 'manage_guild', False)))


def allowed(interaction, kind):
    guild = getattr(interaction, 'guild', None)
    return bool(guild is not None and guild.id == interaction.guild_id and
                (manager(interaction) or kind != 'roll' and moderator(interaction)))


def paragraph(value, limit, label):
    require(isinstance(value, str) and length(value) <= limit and
            not any(ord(c) < 32 and c not in '\n\t' for c in value), f'{label} must be at most {limit} characters.')
    return value.strip()


def command_name(value):
    require(isinstance(value, str), 'Enter a command name.')
    value = value.strip().removeprefix('/').lower()
    require(SLUG.fullmatch(value), 'Use 1–32 lowercase letters, numbers, hyphens, or underscores; start with a letter.')
    require(value not in RESERVED, 'That command name is reserved. Choose another name.')
    return value


def new_field():
    return 'field_' + secrets.token_hex(6)


def odds(source, *, percentages=False):
    """Relative weights or exact percentages, sampled with integer arithmetic."""
    require(isinstance(source, str) and len(source) <= 3500, 'Enter up to 25 lines: outcome | weight.')
    rows = []
    for line in source.splitlines():
        if not line.strip():
            continue
        parts = line.rsplit('|', 1)
        require(len(parts) == 2, 'Use one outcome per line, such as Fire | 25.')
        label = text(parts[0], 40, 'outcome name')
        weight = parts[1].strip().removesuffix('%').strip()
        require(re.fullmatch(r'\d{1,10}(?:\.\d{1,4})?', weight), 'Weights must be positive numbers with up to four decimal places.')
        fraction = Fraction(weight)
        require(0 < fraction <= 1_000_000_000, 'Each weight must be above zero and at most 1 billion.')
        rows.append((label, fraction))
    require(1 <= len(rows) <= 25, 'Add 1–25 outcomes.')
    require(len({label.casefold() for label, _ in rows}) == len(rows), 'Give every outcome a different name.')
    if percentages:
        total = sum(value for _, value in rows)
        require(total == 100, f'Probabilities total {float(total):g}%. Make them add up to exactly 100%.')
    scale = math.lcm(*(value.denominator for _, value in rows))
    weights = [int(value * scale) for _, value in rows]
    divisor = reduce(math.gcd, weights)
    weights = [value // divisor for value in weights]
    require(sum(weights) <= 1_000_000_000, 'These odds need too many tickets. Use smaller or simpler weights.')
    return [{'name': name, 'weight': weight} for (name, _), weight in zip(rows, weights)]


def percentage(weight, total):
    """Round-trip an exact percentage with up to four decimal places."""
    units = Fraction(weight * 1_000_000, total)
    require(units.denominator == 1, 'Use probabilities with at most four decimal places.')
    whole, fraction = divmod(units.numerator, 10_000)
    return str(whole) + ('.' + f'{fraction:04d}'.rstrip('0') if fraction else '')


def odds_text(outcomes, *, percentages=False):
    total = sum(row['weight'] for row in outcomes)
    return '\n'.join(f"{row['name']} | {percentage(row['weight'], total) + '%' if percentages else row['weight']}"
                     for row in outcomes)


def outcome_at(outcomes, ticket):
    if type(ticket) is not int or ticket < 1:
        return None
    boundary = 0
    for row in outcomes:
        boundary += row['weight']
        if ticket <= boundary:
            return row['name']
    return None


def step_plan(step):
    return prepare(step['dice'] if step['type'] == 'dice' else f"1d{sum(r['weight'] for r in step['outcomes'])}")


def condition(source, entries, position):
    """Conditions reference an earlier stable field, never execute code."""
    if not source.strip():
        return None
    require(len(source) <= 200, 'Keep a condition under 200 characters.')
    match = re.fullmatch(r'(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)', source.strip())
    require(match, 'Use an earlier roll name and comparison, such as Bending >= 61 or Nation == Fire.')
    name, op, value = match.groups()
    previous = next((s for s in entries[:position] if s['name'].casefold() == name.strip().casefold()), None)
    require(previous is not None, 'Conditions must use an earlier roll name. Move the source roll above this one first.')
    numeric = previous['type'] == 'dice' and step_plan(previous).numeric
    if numeric:
        require(re.fullmatch(r'[+-]?\d{1,15}', value), 'Compare this numeric roll to a whole number.')
        value = int(value)
    else:
        require(op in ('==', '!='), 'Named outcomes use == or !=.')
        require(previous['type'] in OUTCOME_TYPES or len(step_plan(previous).dice) == 1,
                'Text conditions need a single text die or an odds table.')
        value = text(value, 40, 'comparison outcome')
    return {'field': previous['id'], 'op': op, 'value': value}


def condition_text(entry, entries):
    check = entry.get('when')
    if not check:
        return ''
    source = next((s['name'] for s in entries if s['id'] == check['field']), 'Missing roll')
    return f"{source} {check['op']} {check['value']}"


def validate(data, kind, *, publishing=False):
    require(kind in KINDS and isinstance(data, dict), 'Choose Character rolls, FAQ, or Shop.')
    result = {'title': text(data.get('title'), 80, 'title'),
              'description': paragraph(data.get('description', ''), 700, 'Introduction'),
              'color': data.get('color', THEMES[kind]), 'entries': []}
    require(type(result['color']) is int and 0 <= result['color'] <= 0xFFFFFF, 'Use a six-digit hex colour, such as 8B5CF6.')
    entries = data.get('entries', [])
    require(isinstance(entries, list) and len(entries) <= MAX_ENTRIES, 'Use at most 20 entries per command.')
    require(not publishing or entries, 'Add at least one entry before publishing.')
    ids, names, total_dice = set(), set(), 0
    for raw in entries:
        require(isinstance(raw, dict) and isinstance(raw.get('id'), str) and FIELD.fullmatch(raw['id']), 'Invalid entry identity.')
        name = text(raw.get('name'), 60 if kind == 'faq' else 40, 'entry name')
        require(raw['id'] not in ids and name.casefold() not in names, 'Every entry needs a unique name and ID.')
        item = {'id': raw['id'], 'name': name}
        if kind == 'roll':
            require(not any(c in name for c in '<>='), 'Roll names cannot contain <, >, or =; these are used by conditions.')
            require(raw.get('type') in ('dice', *OUTCOME_TYPES), 'Choose dice, named probabilities, or relative weights.')
            item['type'] = raw['type']
            if item['type'] == 'dice':
                item['dice'] = prepare(raw.get('dice', '')).expression
            else:
                require(isinstance(raw.get('outcomes'), list) and 1 <= len(raw['outcomes']) <= 25 and
                        all(isinstance(r, dict) and isinstance(r.get('name'), str) and type(r.get('weight')) is int
                            and 0 < r['weight'] <= 1_000_000_000 for r in raw['outcomes']), 'Enter valid weighted outcomes.')
                exact = item['type'] == 'probability'
                item['outcomes'] = odds(odds_text(raw['outcomes'], percentages=exact), percentages=exact)
                if raw.get('display_name') is not None:
                    require(exact, 'Player labels are available for named probabilities only.')
                    item['display_name'] = text(raw['display_name'], 40, 'player label')
            total_dice += len(step_plan(item).dice)
            check = raw.get('when')
            if check is not None:
                require(isinstance(check, dict) and check.get('op') in OPS, 'Choose a valid condition.')
                source = next((s for s in result['entries'] if s['id'] == check.get('field')), None)
                require(source is not None, 'A condition references a missing or later roll. Remove its condition or fix the order.')
                item['when'] = condition(f"{source['name']} {check['op']} {check.get('value')}", result['entries'], len(result['entries']))
        elif kind == 'faq':
            item['answer'] = paragraph(raw.get('answer', ''), 1500, 'Answer')
            require(item['answer'], 'Write an answer for this question.')
        else:
            item['description'] = paragraph(raw.get('description', ''), 700, 'Item description')
            require(type(raw.get('price')) is int and 0 <= raw['price'] <= MAX_MONEY, 'Prices must be whole numbers from 0 to 1 trillion.')
            item['price'] = raw['price']
        result['entries'].append(item)
        ids.add(item['id'])
        names.add(name.casefold())
    require(total_dice <= MAX_TEMPLATE_DICE, 'A character template can roll at most 200 base dice in total.')
    if kind == 'shop':
        require(type(data.get('wallet_id')) is int and data['wallet_id'] > 0, 'Choose a wallet for this shop.')
        result['wallet_id'] = data['wallet_id']
    return result


def passes(check, completed):
    if not check:
        return True
    if check['field'] not in completed:
        return False
    left, right, op = completed[check['field']], check['value'], check['op']
    if op == '==': return left == right
    if op == '!=': return left != right
    if op == '>=': return left >= right
    if op == '<=': return left <= right
    if op == '>': return left > right
    return left < right


def roll_definition(document, draw):
    completed, results = {}, []
    for entry in document['entries']:
        if not passes(entry.get('when'), completed):
            continue
        result = draw(entry, step_plan(entry))
        outcome = None
        if entry['type'] in OUTCOME_TYPES:
            outcome = outcome_at(entry['outcomes'], result['total'])
            require(outcome is not None, 'Could not select a valid named outcome.')
        values = values_of(result)
        completed[entry['id']] = outcome if outcome is not None else result['total'] if result['total'] is not None else values[0] if len(values) == 1 else values
        if entry['type'] == 'probability':
            # The internal sample exists only to share the Control pipeline.
            # Neither the player payload nor its replay receipt exposes a die.
            results.append({'id': entry['id'], 'name': entry.get('display_name', entry['name']),
                            'type': 'probability', 'outcome': outcome})
        else:
            results.append({'id': entry['id'], 'name': entry['name'], 'dice': result, 'outcome': outcome})
    return {'title': document['title'], 'description': document['description'], 'color': document['color'], 'results': results}


class CustomCommands:
    def __init__(self, store, owner_id):
        self.store, self.owner_id = store, owner_id
        self.wallets = Wallets(store, owner_id)

    @staticmethod
    def _record(row):
        result = dict(row)
        result['draft'] = json.loads(result['draft'])
        result['published'] = json.loads(result['published']) if result['published'] else None
        return result

    def list(self, guild_id=None, *, published=False, kind=None):
        query, args = 'SELECT * FROM server_commands_v1 WHERE owner=?', [str(self.owner_id)]
        if guild_id is not None:
            query += ' AND guild=?'
            args.append(snowflake(guild_id))
        if published: query += ' AND published IS NOT NULL AND archived=0'
        if kind is not None:
            query += ' AND kind=?'
            args.append(kind)
        return [self._record(row) for row in self.store.db.execute(query + ' ORDER BY id', args)]

    def get(self, guild_id, identity, *, published=False):
        require(type(identity) is int and identity > 0, 'Choose a command first.')
        row = self.store.db.execute('SELECT * FROM server_commands_v1 WHERE owner=? AND guild=? AND id=?',
                                   (str(self.owner_id), snowflake(guild_id), identity)).fetchone()
        require(row is not None, 'That command is not available in this server.')
        result = self._record(row)
        require(not published or result['published'] is not None and not result['archived'], 'That command is not published. Ask a moderator.')
        return result

    def rig_template(self, scope):
        require(isinstance(scope, str) and re.fullmatch(r'custom:[1-9]\d{0,18}', scope), 'Choose a custom character command.')
        identity = int(scope.split(':')[1])
        row = self.store.db.execute('SELECT guild FROM server_commands_v1 WHERE owner=? AND id=?',
                                   (str(self.owner_id), identity)).fetchone()
        require(row is not None, 'This custom command no longer exists.')
        result = self.get(int(row['guild']), identity, published=True)
        require(result['kind'] == 'roll', 'Control works on character rolls, not FAQs or shops.')
        return result

    def rig_label(self, scope, part=None):
        identity = int(scope.split(':')[1])
        row = self.store.db.execute('SELECT * FROM server_commands_v1 WHERE owner=? AND id=?',
                                   (str(self.owner_id), identity)).fetchone()
        if row is None: return f'Custom #{identity} · removed'
        command = self._record(row)
        doc = command['published'] or command['draft']
        name = next((e['name'] for e in doc['entries'] if e['id'] == part), 'Removed roll') if part else 'Whole character'
        return f"/{command['name']} · {name}"

    @staticmethod
    def permission(kind, is_manager, is_moderator):
        require(is_manager is True or kind != 'roll' and is_moderator is True,
                'Character commands require the server owner, Administrator, or Manage Server. FAQs and shops require moderation permissions.')

    def create(self, actor, guild, name, kind, title, description='', *, is_manager=False, is_moderator=False):
        require(kind in KINDS, 'Choose a command type.')
        self.permission(kind, is_manager, is_moderator)
        actor, guild_key = snowflake(actor), snowflake(guild)
        name = command_name(name)
        data = {'title': title, 'description': description, 'color': THEMES[kind], 'entries': []}
        if kind == 'shop': data['wallet_id'] = self.wallets.ensure_default(guild)['id']
        data = validate(data, kind)
        with self.store.transaction():
            require(len(self.list(guild)) < MAX_COMMANDS, 'This server already has 20 command slots. Reuse an archived command.')
            require(not any(c['name'] == name for c in self.list(guild)), 'That command name already exists here.')
            identity = self.store.db.execute('INSERT INTO server_commands_v1(owner,guild,name,kind,draft,actor) VALUES (?,?,?,?,?,?)',
                (str(self.owner_id), guild_key, name, kind, json.dumps(data), actor)).lastrowid
            return self.get(guild, identity)

    def save(self, actor, guild, identity, revision, data, *, name=None, is_manager=False, is_moderator=False):
        with self.store.transaction():
            current = self.get(guild, identity)
            self.permission(current['kind'], is_manager, is_moderator)
            require(current['revision'] == revision, 'This draft changed elsewhere. Select the command again, then reopen the form.')
            data = validate(data, current['kind'])
            if current['kind'] == 'shop': self.wallets.get(guild, data['wallet_id'])
            name = command_name(name) if name is not None else current['name']
            require(name == current['name'] or current['published'] is None, 'Unpublish before changing the slash-command name.')
            require(not any(c['name'] == name and c['id'] != identity for c in self.list(guild)), 'That command name already exists here.')
            self.store.db.execute('UPDATE server_commands_v1 SET draft=?,name=?,revision=revision+1,actor=? WHERE id=?',
                                 (json.dumps(data), name, snowflake(actor), identity))
            return self.get(guild, identity)

    def visibility(self, actor, guild, identity, revision, action, *, is_manager=False, is_moderator=False):
        require(action in ('publish', 'unpublish', 'archive', 'restore'), 'Choose a valid publishing action.')
        with self.store.transaction():
            current = self.get(guild, identity)
            self.permission(current['kind'], is_manager, is_moderator)
            require(current['revision'] == revision, 'This command changed elsewhere. Select it again before publishing.')
            data = validate(current['draft'], current['kind'], publishing=action == 'publish')
            if current['kind'] == 'shop': self.wallets.get(guild, data['wallet_id'])
            live = json.dumps(data) if action == 'publish' else None
            self.store.db.execute('UPDATE server_commands_v1 SET published=?,archived=?,revision=revision+1,live_revision=?,actor=? WHERE id=?',
                (live, int(action == 'archive'), current['revision'] + 1 if live else 0, snowflake(actor), identity))
            return self.get(guild, identity)

    def purchase(self, player, guild, identity, item_id, quantity, revision, *, request_id):
        require(type(quantity) is int and 1 <= quantity <= 100, 'Buy 1–100 items at a time.')
        require(request_id is not None, 'Open a new checkout confirmation.')
        player_key, guild_key = snowflake(player), snowflake(guild)
        signature = json.dumps(['shop', player_key, guild_key, identity, item_id, quantity, revision])
        with self.store.transaction():
            cached = self.wallets._receipt(request_id, signature)
            if cached is not None: return {**cached, 'replayed': True}
            command = self.get(guild, identity, published=True)
            require(command['kind'] == 'shop' and command['live_revision'] == revision, 'This shop changed. Open it again to review the current price.')
            document = command['published']
            item = next((i for i in document['entries'] if i['id'] == item_id), None)
            require(item is not None, 'This item is no longer sold here.')
            wallet = self.wallets.get(guild, document['wallet_id'])
            total = item['price'] * quantity
            require(total <= MAX_MONEY, 'This purchase exceeds the currency limit.')
            row = self.store.db.execute('SELECT balance FROM currency_balances_v1 WHERE wallet=? AND player=?',
                                       (wallet['id'], player_key)).fetchone()
            balance = row['balance'] if row else 0
            require(balance >= total, f'You need {total:,} {wallet["currency"]}; your balance is {balance:,}. Nothing was bought.')
            row = self.store.db.execute('SELECT quantity FROM shop_inventory_v1 WHERE command=? AND item=? AND player=?',
                                       (identity, item_id, player_key)).fetchone()
            owned = (row['quantity'] if row else 0) + quantity
            require(owned <= 1_000_000, 'You have reached the inventory limit for this item.')
            self.store.db.execute('INSERT INTO currency_balances_v1 VALUES (?,?,?) ON CONFLICT(wallet,player) DO UPDATE SET balance=excluded.balance',
                                  (wallet['id'], player_key, balance - total))
            self.store.db.execute('INSERT INTO currency_ledger_v1(wallet,player,actor,delta,balance_after,reason) VALUES (?,?,?,?,?,?)',
                                  (wallet['id'], player_key, player_key, -total, balance - total, f"Shop /{command['name']}: {item['name']} ×{quantity}"))
            self.store.db.execute('INSERT INTO shop_inventory_v1(command,item,player,name,quantity) VALUES (?,?,?,?,?) '
                                 'ON CONFLICT(command,item,player) DO UPDATE SET name=excluded.name,quantity=excluded.quantity',
                                  (identity, item_id, player_key, item['name'], owned))
            result = {'item': item['name'], 'quantity': quantity, 'total': total, 'balance': balance - total,
                      'owned': owned, 'wallet': wallet, 'shop': command['name'], 'replayed': False}
            self.wallets._remember(request_id, signature, result)
            return result

    def inventory(self, player, guild, identity):
        self.get(guild, identity)
        return [dict(row) for row in self.store.db.execute('SELECT name,quantity FROM shop_inventory_v1 WHERE command=? AND player=? ORDER BY item',
                                                         (identity, snowflake(player)))]
