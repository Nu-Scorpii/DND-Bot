"""Private stacked controls, legal dice, and atomic complete-character receipts."""

import json
from .avatar import STAT_PARTS, create_character
from .controls import COMMAND, HELP, MAX_RIGS, category, describe, empty_setting, panel_setting, parse_command, player_id, rule_key, is_custom_scope
from .custom import CustomCommands, roll_definition, step_plan, validate
from .dice import RNG, prepare, values_of
from .luck import extra_dice_result, luck_result
from .solver import Unavailable, force_face, force_total
from .errors import RollError, require


class DiceEngine:
    def __init__(self, store, owner_id, rng=RNG):
        require(type(owner_id) is int and owner_id > 0, 'BOT_OWNER_ID must be your Discord user ID.')
        self.store, self.owner_id, self.rng = store, owner_id, rng
        self.customs = CustomCommands(store, owner_id)

    def control_status(self, author_id, *, is_dm):
        require(is_dm and author_id == self.owner_id, 'These controls are private to the bot creator.')
        return self.store.controls(self.owner_id)

    def configure(self, author_id, *, is_dm, mode, value=None, target=None,
                  scope='roll', part=None, uses='default', request_id=None):
        """Only the owner configures controls; any selected player can receive them."""
        require(is_dm and author_id == self.owner_id, 'These controls are private to the bot creator.')
        new = panel_setting(mode, value, self.owner_id if target is None else target,
                            scope=scope, part=part, uses=uses)
        if mode != 'off' and is_custom_scope(scope):
            template = self.customs.rig_template(scope)
            if part is not None:
                entry = next((s for s in template['published']['entries'] if s['id'] == part), None)
                require(entry is not None, 'That custom roll was removed. Choose its current template again.')
                plan = step_plan(entry)
                require(mode != 'face' or not plan.numeric, 'Use Force with an outcome preset for numeric dice and odds tables.')
                require(mode not in ('force', 'shift') or plan.numeric, 'This roll uses text faces. Choose Text face or Extra dice.')
        signature = json.dumps(['apply', author_id, new], sort_keys=True, ensure_ascii=False)
        with self.store.transaction():
            cached = self.store.receipt('panel', request_id, signature)
            if cached is not None:
                return cached
            controls = self.store.controls(self.owner_id)
            if mode == 'off':
                controls['rules'] = []
            else:
                existing = next((r for r in controls['rules'] if rule_key(r) == rule_key(new)), None)
                if existing:
                    existing.update({key: new[key] for key in ('mode', 'value', 'target', 'scope', 'part', 'remaining')})
                else:
                    require(len(controls['rules']) < MAX_RIGS, '100 settings are already active. Remove an unused setting first.')
                    controls['rules'].append({key: new[key] for key in ('mode', 'value', 'target', 'scope', 'part', 'remaining')} |
                                             {'id': controls['next_id']})
                    controls['next_id'] += 1
            self.store.save_controls(self.owner_id, controls)
            self.store.remember('panel', request_id, signature, controls)
            return controls

    def remove_control(self, author_id, *, is_dm, rule_id=None, target=None, request_id=None):
        """Remove one stable rule ID, or all rules for one explicitly chosen player."""
        require(is_dm and author_id == self.owner_id, 'These controls are private to the bot creator.')
        require((rule_id is None) != (target is None), 'Choose a setting or a player to clear.')
        if rule_id is not None:
            require(type(rule_id) is int and rule_id > 0, 'Choose an active setting.')
        else:
            target = player_id(target)
        signature = json.dumps(['remove', author_id, rule_id, target])
        with self.store.transaction():
            cached = self.store.receipt('panel', request_id, signature)
            if cached is not None:
                return cached
            controls = self.store.controls(self.owner_id)
            controls['rules'] = [r for r in controls['rules'] if
                                 (r['id'] != rule_id if rule_id is not None else r['target'] != target)]
            self.store.save_controls(self.owner_id, controls)
            self.store.remember('panel', request_id, signature, controls)
            return controls

    def private_message(self, author_id, content, *, is_dm, request_id=None):
        if not is_dm or author_id != self.owner_id or not isinstance(content, str) or not COMMAND.match(content):
            return None
        try:
            action, new = parse_command(content)
        except RollError as exc:
            return str(exc)
        signature = json.dumps([self.owner_id, content])
        with self.store.transaction():
            cached = self.store.receipt('dm', request_id, signature)
            if cached is not None:
                return cached
            setting = self.store.setting(self.owner_id)
            if action == 'help':
                reply = HELP
            elif action == 'status':
                reply = describe(setting) + ('\n\nLast affected roll:\n' + setting['last'] if setting['last'] else '')
            else:
                new = empty_setting() if action == 'off' else new
                new['last'] = setting['last']
                self.store.save_setting(self.owner_id, new)
                reply = describe(new)
            self.store.remember('dm', request_id, signature, reply)
            return reply

    def roll(self, user_id, guild_id, expression=None, *, faces=None, bonus=0, keep='all', request_id=None):
        # Invalid input must never draw dice or consume a one-use setting.
        plan = prepare(expression, faces=faces, bonus=bonus, keep=keep)
        return self.roll_plan(user_id, guild_id, plan, request_id=request_id)

    @staticmethod
    def _matches(rule, user_id, part, scope=None):
        if is_custom_scope(scope):
            return ((rule['target'] is None or rule['target'] == str(user_id)) and rule['scope'] == scope
                    and (rule['part'] is None or rule['part'] == part))
        return ((rule['target'] is None or rule['target'] == str(user_id))
                and rule['scope'] in ('all', 'avatar' if part is not None else 'roll')
                and (rule['part'] is None or rule['part'] == part
                     or part in STAT_PARTS and rule['part'] == 'stats'))

    @staticmethod
    def _consume(controls, used):
        """Each affected command spends at most one use of each setting, atomically."""
        for rule in controls['rules']:
            if rule['id'] in used and rule['remaining'] is not None:
                rule['remaining'] -= 1
        controls['rules'] = [rule for rule in controls['rules'] if rule['remaining'] != 0]

    def _draw(self, user_id, plan, controls, used, *, part=None, natural=None, exact_only=False, scope=None, label=None):
        """Apply extra dice, luck, shift, then force; only one rule of each kind wins."""
        natural = plan.roll(self.rng) if natural is None else natural
        result, notices, records = natural, [], []
        candidates = [r for r in controls['rules'] if self._matches(r, user_id, part, scope)
                      and (not exact_only or r['part'] == part and category(r) not in ('luck', 'extra'))
                      # An old unrestricted exact control changes only the first
                      # eligible part of an Avatar, as it did before durations.
                      and not (r['scope'] == 'all' and r['part'] is None
                               and category(r) not in ('luck', 'extra') and r['id'] in used)]
        chosen = {}
        def priority(rule):
            specificity = 2 if rule['part'] == part else 1 if rule['part'] == 'stats' else 0
            return specificity, rule['target'] is not None, rule['scope'] != 'all', rule['id']
        for rule in candidates:
            kind = category(rule)
            if kind not in chosen or priority(rule) > priority(chosen[kind]):
                chosen[kind] = rule
        for kind in ('extra', 'luck', 'shift', 'force', 'face'):
            if kind not in chosen:
                continue
            rule = chosen[kind]
            mode, value = rule['mode'], rule['value']
            before = result
            try:
                if kind == 'extra':
                    result = extra_dice_result(plan, result, mode, value, self.rng)
                elif kind == 'luck':
                    result = luck_result(plan, result, mode, value)
                elif mode == 'face':
                    result = force_face(plan, result, value)
                else:
                    if result['total'] is None:
                        raise Unavailable('text dice have no numeric total; choose Text face in the private panel')
                    target = value if mode == 'force' else result['total'] + value
                    result = force_total(plan, result, target)
            except Unavailable as exc:
                unchanged = ('The roll stayed normal' if result == natural else 'Other successful changes were kept')
                duration = ('this setting stays permanent' if rule['remaining'] is None else
                            'this one-use setting is cleared' if rule['remaining'] == 1 else
                            f"one use is spent; {rule['remaining'] - 1} remain")
                notices.append(f"{((label or 'Avatar ' + part) + ': ') if part else ''}setting #{rule['id']} could not apply: "
                               f'{exc}. {unchanged}; {duration}.')
            detail = f"{mode} {value}{'%' if kind == 'luck' else ''}"
            if kind == 'extra':
                detail = f"{value} extra dice each · keep {'highest' if mode == 'extra_high' else 'lowest'}"
            record = f"{label or part or '/roll'} · #{rule['id']} {detail}: {before['total']} → {result['total']}"
            if kind == 'luck' and not plan.numeric:
                record += ' · Luck skipped: text dice have no numeric order.'
            elif mode == 'face':
                record += f' · {value}'
            records.append(record)
            if kind != 'luck' or plan.numeric:
                used.add(rule['id'])
        return result, notices, records

    def roll_plan(self, user_id, guild_id, plan, *, request_id=None):
        signature = json.dumps([self.owner_id, str(user_id), str(guild_id), plan.expression, plan.terms, plan.bonus, plan.keep],
                               default=lambda die: die.__dict__, sort_keys=True, ensure_ascii=False)
        with self.store.transaction():
            cached = self.store.receipt('roll', request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            controls = self.store.controls(self.owner_id)
            used = set()
            result, notices, records = self._draw(user_id, plan, controls, used)
            self._consume(controls, used)
            if records:
                controls['last'] = (f'User {user_id} · server {guild_id}\n' + '\n'.join(records + notices))[:1000]
                self.store.save_controls(self.owner_id, controls)
            outcome = {'dice': result, 'notice': '\n'.join(notices) or None, 'replayed': False}
            self.store.remember('roll', request_id, signature, outcome)
            return outcome

    def avatar(self, user_id, guild_id, *, request_id=None):
        signature = json.dumps([self.owner_id, str(user_id), str(guild_id), 'avatar-v1'])
        with self.store.transaction():
            cached = self.store.receipt('avatar', request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            controls = self.store.controls(self.owner_id)
            notices, records, drawn, used = [], [], set(), set()

            def draw(part, plan):
                drawn.add(part)
                if part == 'stats':
                    pool = plan.roll(self.rng)
                    # Preserve a legacy six-die total control before individual slots.
                    pool, notes, history = self._draw(user_id, plan, controls, used,
                                                     part='stats', natural=pool, exact_only=True)
                    notices.extend(notes)
                    records.extend(history)
                    single = prepare('1d20')
                    values = []
                    for slot, value in zip(STAT_PARTS, values_of(pool)):
                        drawn.add(slot)
                        result, notes, history = self._draw(user_id, single, controls, used, part=slot,
                                                           natural=single.result([value]))
                        values.append(result['total'])
                        notices.extend(notes)
                        records.extend(history)
                    return plan.result(values)
                result, notes, history = self._draw(user_id, plan, controls, used, part=part)
                notices.extend(notes)
                records.extend(history)
                return result

            character = create_character(draw)
            # A finite conditional setting reserves character uses, even when that
            # character does not need its branch. Permanent settings remain active.
            skipped = [r for r in controls['rules'] if r['scope'] in ('avatar', 'all')
                       and r['part'] is not None and r['part'] not in drawn
                       and (r['target'] is None or r['target'] == str(user_id)) and r['remaining'] is not None]
            for rule in skipped:
                duration = (f"One-use setting #{rule['id']} is cleared." if rule['remaining'] == 1 else
                            f"setting #{rule['id']} spent one character use; {rule['remaining'] - 1} remain.")
                notices.append(f"Avatar {rule['part']} was not rolled for user {user_id}: this character did not need it. "
                               + duration)
                used.add(rule['id'])
            self._consume(controls, used)
            if records or notices:
                controls['last'] = (f'User {user_id} · server {guild_id}\n' + '\n'.join(records + notices))[:1000]
                self.store.save_controls(self.owner_id, controls)
            outcome = {'character': character, 'notice': '\n'.join(notices) or None, 'replayed': False}
            self.store.remember('avatar', request_id, signature, outcome)
            return outcome

    def custom_roll(self, user_id, guild_id, command_id, *, request_id=None):
        signature = json.dumps([self.owner_id, str(user_id), str(guild_id), command_id, 'custom-v1'])
        with self.store.transaction():
            cached = self.store.receipt('custom', request_id, signature)
            if cached is not None:
                return {**cached, 'replayed': True}
            command = self.customs.get(guild_id, command_id, published=True)
            require(command['kind'] == 'roll', 'Choose a character roll command.')
            document = validate(command['published'], 'roll', publishing=True)
            scope, used, drawn = f'custom:{command_id}', set(), set()
            controls = self.store.controls(self.owner_id)
            notices, records = [], []
            def draw(entry, plan):
                drawn.add(entry['id'])
                result, notes, history = self._draw(user_id, plan, controls, used, part=entry['id'], scope=scope,
                                                   label=f"/{command['name']} · {entry['name']}")
                notices.extend(notes)
                records.extend(history)
                return result
            character = roll_definition(document, draw)
            for rule in controls['rules']:
                if (rule['scope'] == scope and rule['part'] is not None and rule['part'] not in drawn
                        and rule['target'] in (None, str(user_id)) and rule['remaining'] is not None):
                    used.add(rule['id'])
                    notices.append(f"/{command['name']} · setting #{rule['id']}: its roll was skipped or removed; one character use spent.")
            self._consume(controls, used)
            if records or notices:
                controls['last'] = (f'User {user_id} · server {guild_id}\n' + '\n'.join(records + notices))[:1000]
                self.store.save_controls(self.owner_id, controls)
            outcome = {'character': character, 'notice': '\n'.join(notices) or None, 'replayed': False}
            self.store.remember('custom', request_id, signature, outcome)
            return outcome
