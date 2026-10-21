"""Creator-only DM panel. Draft edits never arm a control until Apply is pressed."""

import logging
import secrets
import discord
from .controls import EXTRA_MODES, MAX_EXTRA_DICE, PASSIVE_MODES, category, panel_setting, player_id, rig_uses, is_custom_scope
from .custom import OUTCOME_TYPES, outcome_at, step_plan
from .display import chunks, safe, short
from .errors import RollError

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()

ROLLS = {
    'roll': ('/roll · Custom dice', 'Change the final total, including bonuses.'),
    'avatar': ('/avatar · Whole character', 'Luck or extra dice on all applicable character rolls.'),
    'iq': ('/avatar · IQ', 'One d20: 1–20.'),
    'stats': ('/avatar · All six stats', 'Luck or extra dice on all six. Use Stat 1–6 for exact results.'),
    **{f'stat{index}': (f'/avatar · Stat {index}', f'Change only die {index} of the ordered 6d20 roll: 1–20.')
       for index in range(1, 7)},
    'nation': ('/avatar · Nation', 'Fire, Water, Earth, or Nomad.'),
    'tribe': ('/avatar · Water tribe', 'Southern, Northern, or Swamp; Water characters only.'),
    'family': ('/avatar · Fire family', 'Civilian, Military, High ranking, or Royal.'),
    'control': ('/avatar · Earth village', 'Whether the village is under Fire Nation control.'),
    'bending': ('/avatar · Bending check', '1–60 non-bender; 61–100 bender.'),
    'element': ('/avatar · Nomad element', 'Fire, Water, or Earth; bending must be above 60.'),
    'bending_strength': ('/avatar · Bending Strength', 'Separate d20: 1–20, for benders only.'),
    'combat': ('/avatar · Non-bender combat', 'Sword, Bow, Fan, Fists, or Pressure point.'),
}
MODES = {
    'force': ('Force a result', 'Choose an exact total, including any + or - in /roll.'),
    'shift': ('Add / subtract points', 'Enter +3, -2, or another whole number.'),
    'good': ('Good luck', '1–100% strength toward the highest legal result.'),
    'bad': ('Bad luck', '1–100% strength toward the lowest legal result.'),
    'extra_high': ('Extra dice · keep highest', 'Roll extra copies of each die, showing only its highest result.'),
    'extra_low': ('Extra dice · keep lowest', 'Roll extra copies of each die, showing only its lowest result.'),
    'face': ('Text face', 'Choose an exact label from a custom /roll die.'),
}
PRESETS = {
    'nation': [('Fire Nation', 1), ('Water Tribes', 26), ('Earth Kingdom', 51), ('Nomad', 76)],
    'tribe': [('Southern tribe', 1), ('Northern tribe', 2), ('Swamp tribe', 3)],
    'family': [('Civilian family', 1), ('Military family', 50), ('High ranking family', 80), ('Royal family', 95)],
    'control': [('Under Fire Nation control', 1), ('Not under Fire Nation control', 16)],
    'bending': [('Bender', 61), ('Non-bender', 60)],
    'element': [('Fire bending', 1), ('Water bending', 2), ('Earth bending', 3)],
    'combat': [('Sword', 1), ('Bow', 21), ('Fan', 41), ('Fists', 61), ('Pressure point', 81)],
}
HELP = ('1. Choose the roll you want to change.\n'
        '2. Choose Force, Add/subtract, Luck, Extra dice, or Text face.\n'
        '3. Choose player, or use Target me.\n'
        '4. Pick an outcome or Enter value.\n'
        '5. Use Duration: enter a number of uses or permanent, then Apply.\n\n'
        'Only Apply changes live rolls. Add several Avatar settings for the same player. '
        'Apply replaces only the same player + roll + kind; other settings stay active. '
        'Good/bad share one luck slot. Every setting can last 1–1,000,000 uses or be permanent. '
        'Defaults are one use for force/shift/text and permanent for luck or extra dice. '
        'One /roll command is one use, even with several dice. One /avatar character '
        'is one use per affected setting, even for whole-character or all-six-stats luck. '
        'Finite conditional-part settings also spend one character use if that part is skipped. '
        'Permanent settings stay until removed. Active settings shows remaining uses, with player filtering, '
        'Edit selected, Remove selected, Clear player, and Clear all. '
        'Player targets work in every server; no server ID is needed.\n\n'
        'Luck strength is 1–100%, not a chance to activate. Each numeric die moves that '
        'fraction toward its best/worst face, rounded to a legal face. 50% moves halfway; '
        '100% reaches the extreme. Very small strengths may round to no change. '
        'Signed dice respect the final total. Text dice skip luck.\n\n'
        'Extra dice: choose Keep highest or Keep lowest, then Extra dice. '
        f'Enter 1–{MAX_EXTRA_DICE} extra dice per result: 4 extra means five candidates, keeping one. '
        'Each original die uses its own type and faces; weighted faces keep their weights. '
        'A 6d20 still shows six results. Discarded candidates and the extra count stay hidden. '
        'Highest/lowest compares each die face before signed arithmetic, modifiers, or keep-high/low. '
        'Text/mixed dice rank faces in listed order (first distinct face lowest, last highest); '
        'repeated faces add weight without changing their rank. '
        'Highest/lowest share one extra-dice slot; percentage luck is a separate slot.\n\n'
        'Choose Stat 1–6 to force or shift each of the six d20s separately. All six stats '
        'and Whole character support luck and extra dice. A stat-specific setting overrides all-stats '
        'and then whole-character settings of the same kind. Extra dice, then luck, shift, and exact force '
        'runs on each part; force wins. Higher/lower means the number, not which nation is best. '
        'A /roll setting never changes /avatar. '
        'Other players, other command scopes, invalid commands, and duplicate requests do not spend uses. '
        'Impossible changes spend one use; permanent settings stay. Only you get the explanation. '
        'Apply resets just that slot to the duration shown in the draft.\n\n'
        'The Nomad element d3 runs only for Nomad characters with bending above 60. '
        'It never creates Air bending. Every bender also rolls a separate Bending Strength d20. '
        'Choose Bending Strength to control it, or Whole character to include it with other parts. '
        'Non-benders skip that die; a strength setting cannot make someone a bender. '
        'Finite strength settings follow the same skipped-part duration rule.\n\n'
        'Custom rolls opens the server template picker. Choose a named command, then Whole character '
        'or a named roll. No server ID is needed. Custom settings only affect that template in its own server; '
        'they never alter /avatar or /roll. Named probabilities and weighted tables have named Force presets; highest/lowest '
        'and luck follow the outcome order, not which name sounds best. Conditions use final controlled values. '
        'Named probability results show only the chosen name; their internal selection and setting details stay private. '
        'A template spends one use per affected setting. Finite skipped-part settings follow the Avatar rule.\n\n'
        'To copy a player ID: Discord Settings → Advanced → Developer Mode, then '
        'right-click their profile → Copy User ID. '
        'After a restart or an expired panel, DM !control again.')
PAGE_SIZE = 8


def roll_label(rule, customs=None):
    if is_custom_scope(rule['scope']):
        return customs.rig_label(rule['scope'], rule['part']) if customs else f"{rule['scope']} · {rule['part'] or 'Whole character'}"
    key = rule['part'] or rule['scope']
    return ROLLS.get(key, ('Legacy /roll + /avatar', ''))[0]


def change_value(rule, customs=None):
    mode, value = rule['mode'], rule['value']
    if value is None:
        return 'Enter a value'
    if customs is not None and mode == 'force' and is_custom_scope(rule['scope']):
        try:
            template = customs.rig_template(rule['scope'])
            entry = next((e for e in template['published']['entries'] if e['id'] == rule['part']), None)
            name = outcome_at(entry['outcomes'], value) if entry and entry['type'] in OUTCOME_TYPES else None
            if name is not None: return f'Force {name}'
        except RollError:
            pass  # Removed/unpublished templates still have removable active settings.
    if mode in ('good', 'bad'):
        return f"{'Good' if mode == 'good' else 'Bad'} luck {value}%"
    if mode in EXTRA_MODES:
        return f"Keep {'highest' if mode == 'extra_high' else 'lowest'} · +{value} dice ({value + 1} total each)"
    if mode == 'shift':
        return f'Add {value:+d}' if value >= 0 else f'Subtract {abs(value)}'
    return f"{'Force' if mode == 'force' else 'Text face'} {value}"


def change_label(rule, customs=None):
    return f"{change_value(rule, customs)} · {duration_label(rule.get('remaining', rig_uses(rule['mode'])))}"


def duration_label(remaining):
    return 'Permanent' if remaining is None else f"{remaining:,} {'use' if remaining == 1 else 'uses'} left"


async def private_reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_MENTIONS)


class PanelSelect(discord.ui.Select):
    def __init__(self, panel, kind, options, *, row, placeholder, disabled=False):
        self.panel, self.kind = panel, kind
        super().__init__(custom_id=panel.component_id(kind), options=options, row=row,
                         placeholder=placeholder, disabled=disabled)

    async def callback(self, interaction):
        if not await self.panel.authorize(interaction):
            return
        value = self.values[0]
        if self.kind == 'custom':
            template = self.panel.bot.customs.rig_template(value)
            self.panel.draft.update(scope=value, part=None, mode='good', value=50)
            self.panel.roll, self.panel.page = value, 'edit'
            self.panel.note = f"/{template['name']} selected. Choose a roll, then Apply."
            await self.panel.update(interaction)
            return
        if self.kind == 'roll':
            if value not in self.panel.roll_options():
                await private_reply(interaction, 'Choose a roll from this panel.')
                return
            if value != self.panel.roll:
                self.panel.draft['value'] = None
            self.panel.roll = value
            if is_custom_scope(self.panel.draft['scope']):
                self.panel.draft['part'] = None if value == self.panel.draft['scope'] else value
            else:
                self.panel.draft['scope'] = 'roll' if value == 'roll' else 'avatar'
                self.panel.draft['part'] = None if value in ('roll', 'avatar') else value
            if self.panel.draft['mode'] not in self.panel.allowed_modes():
                mode = 'good' if 'good' in self.panel.allowed_modes() else self.panel.allowed_modes()[0]
                self.panel.draft.update(mode=mode, value=50 if mode == 'good' else None)
            elif self.panel.draft['mode'] in ('good', 'bad') and self.panel.draft['value'] is None:
                self.panel.draft['value'] = 50
            elif self.panel.draft['mode'] in EXTRA_MODES and self.panel.draft['value'] is None:
                self.panel.draft['value'] = 4
        elif self.kind == 'mode':
            if value not in self.panel.allowed_modes():
                await private_reply(interaction, 'Choose a mode from this panel.')
                return
            if value != self.panel.draft['mode']:
                same_kind = category({'mode': value}) == category(self.panel.draft)
                default = 50 if value in ('good', 'bad') else 4 if value in EXTRA_MODES else None
                self.panel.draft['value'] = self.panel.draft['value'] if same_kind else default
            self.panel.draft['mode'] = value
        elif self.kind == 'active':
            active = self.panel.bot.engine.control_status(interaction.user.id, is_dm=True)
            if not value.isdecimal() or not any(r['id'] == int(value) for r in active['rules']):
                await private_reply(interaction, 'That setting has already been used or removed. Press Refresh.')
                return
            self.panel.selected_id = int(value)
            self.panel.note = f'Selected setting #{value}.'
            await self.panel.update(interaction)
            return
        else:
            presets = {str(number) for _, number in self.panel.quick_choices()}
            if value not in presets:
                await private_reply(interaction, 'Choose an outcome for the currently selected roll.')
                return
            self.panel.draft['value'] = int(value)
        self.panel.note = 'Unsaved changes · Press Apply.'
        await self.panel.update(interaction)


class PanelInput(discord.ui.Modal):
    def __init__(self, panel, kind):
        self.panel, self.kind = panel, kind
        self.mode, self.roll = panel.draft['mode'], panel.roll
        self.target = panel.draft['target']
        luck = self.mode in ('good', 'bad')
        extra = self.mode in EXTRA_MODES
        super().__init__(title='Choose player' if kind == 'target' else 'setting duration' if kind == 'duration' else
                         'Luck strength' if luck else 'Extra dice' if extra else 'Enter value', timeout=180)
        current = (rig_uses(self.mode, panel.draft.get('uses', 'default')) if kind == 'duration' else
                   panel.draft['target' if kind == 'target' else 'value'])
        self.field = discord.ui.TextInput(
            default='permanent' if kind == 'duration' and current is None else str(current) if current is not None else None,
            placeholder='Paste their user ID or mention' if kind == 'target' else
                        '3, 10, or permanent' if kind == 'duration' else
                        '1–100, such as 50' if luck else
                        '4 extra = 5 candidates; keep one' if extra else
                        'Exact face label' if self.mode == 'face' else 'Whole number, such as 15 or -2',
            min_length=1, max_length=40)
        self.add_item(discord.ui.Label(text='Player user ID or mention' if kind == 'target' else
                                      'Number of uses, or permanent' if kind == 'duration' else 'Strength (%)' if luck else
                                      f'Extra dice per result (1–{MAX_EXTRA_DICE})' if extra else 'Value',
                                      component=self.field))

    async def interaction_check(self, interaction):
        return await self.panel.authorize(interaction)

    async def on_submit(self, interaction):
        # Recheck at submission as well as dispatch; the panel may have expired.
        if not await self.panel.authorize(interaction):
            return
        try:
            if self.panel.page != 'edit' or self.target != self.panel.draft['target']:
                raise RollError('The panel or player changed. Open the form again.')
            if self.kind == 'target':
                target = player_id(self.field.value)
                await interaction.response.defer()
                try:
                    user = self.panel.bot.get_user(int(target)) or await self.panel.bot.fetch_user(int(target))
                except discord.NotFound:
                    raise RollError('That user was not found. Copy the player\'s user ID, not a server or application ID.')
                except discord.HTTPException:
                    raise RollError('Could not check that player. Try Choose player again.')
                if user.bot:
                    raise RollError('Choose a player account, not a bot account.')
                if self.panel.is_finished():
                    raise RollError('This panel has closed. DM !control to open a new one.')
                self.panel.draft['target'] = target
            else:
                if self.mode != self.panel.draft['mode'] or self.roll != self.panel.roll:
                    raise RollError('The selected roll or mode changed. Open the form again.')
                if self.kind == 'duration':
                    self.panel.draft['uses'] = rig_uses(self.mode, self.field.value)
                else:
                    draft = {**self.panel.draft, 'value': self.field.value}
                    new = panel_setting(**draft)
                    self.panel.draft['value'] = new['value']
            self.panel.note = 'Unsaved changes · Press Apply.'
            await self.panel.update(interaction)
        except RollError as exc:
            await private_reply(interaction, '⚠️ ' + safe(exc))

    async def on_error(self, interaction, error):
        log.warning('Private panel input failed: %s.', type(error).__name__)
        await private_reply(interaction, 'Could not update the draft. Open !control again.')


class RiggerPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.bot, self.message = bot, None
        self.nonce = secrets.token_hex(6)
        active = bot.engine.control_status(bot.engine.owner_id, is_dm=True)
        self.page, self.active_page, self.selected_id, self.player_only = 'edit', 0, None, False
        self.custom_page = 0
        if not active['rules']:
            self.draft = {'mode': 'force', 'value': None, 'target': str(bot.engine.owner_id),
                          'scope': 'roll', 'part': None, 'uses': 'default'}
        else:
            recent = active['rules'][-1]
            self.draft = {key: recent[key] for key in ('mode', 'value', 'target', 'scope', 'part')}
            self.draft['uses'] = recent['remaining']
            # Legacy unrestricted rules stay active until Apply or Turn off.
            # New panel rules always have an explicit command scope.
            if self.draft['scope'] == 'all':
                self.draft['scope'] = 'avatar' if self.draft['part'] else 'roll'
            if self.draft['target'] is None:
                self.draft['target'] = str(bot.engine.owner_id)
            if self.draft['part'] == 'stats' and self.draft['mode'] not in PASSIVE_MODES:
                self.draft.update(mode='good', value=50)
        self.roll = self.draft['part'] or self.draft['scope']
        self.note = 'Choose a roll and change, then Apply.'
        self.build_items()

    def component_id(self, key):
        return f'rigger:{self.nonce}:{key}'

    async def authorize(self, interaction):
        if interaction.user.id != self.bot.engine.owner_id or interaction.guild_id is not None:
            await private_reply(interaction, 'These controls are private to the bot creator.')
            return False
        if self.is_finished():
            await private_reply(interaction, 'This panel has closed. DM !control to open a new one.')
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.authorize(interaction)

    def build_items(self):
        self.clear_items()
        if self.page == 'active':
            self.build_active_items()
            return
        if self.page == 'edit' and is_custom_scope(self.draft['scope']):
            try:
                options = self.roll_options()
                if self.roll not in options:
                    self.roll = self.draft['scope']
                    self.draft.update(part=None, mode='good', value=50)
                    self.note = 'That roll was removed. Choose a current roll before Apply.'
            except RollError:
                self.page = 'customs'
                self.note = 'That template is unpublished. Choose a published custom command.'
        if self.page == 'customs':
            self.build_custom_items()
            return
        self.add_item(PanelSelect(self, 'roll', [discord.SelectOption(label=label, value=key,
            description=hint, default=key == self.roll) for key, (label, hint) in self.roll_options().items()],
            row=0, placeholder='Choose the roll'))
        self.add_item(PanelSelect(self, 'mode', [discord.SelectOption(label=label, value=key,
            description=hint, default=key == self.draft['mode']) for key, (label, hint) in MODES.items()
            if key in self.allowed_modes()],
            row=1, placeholder='Choose what to change'))
        quick = self.quick_choices()
        entry = self.custom_entry()
        named = entry is not None and entry['type'] in OUTCOME_TYPES and self.draft['mode'] == 'force'
        options = [discord.SelectOption(label=label, value=str(value), description=
                   f'Luck strength {value}%.' if self.draft['mode'] in ('good', 'bad') else
                   f'{value + 1} candidates per die; show one.' if self.draft['mode'] in EXTRA_MODES else
                   'Select this named outcome.' if named else
                   f'Forces the die to {value}.',
                   default=value == self.draft['value']) for label, value in quick]
        if quick:
            self.add_item(PanelSelect(self, 'outcome', options, row=2,
                          placeholder='Choose extra dice' if self.draft['mode'] in EXTRA_MODES else 'Pick strength or outcome'))
        for label, key, handler, row, style in [
            ('Choose player', 'target', self.choose_target, 3, discord.ButtonStyle.secondary),
            ('Target me', 'me', self.target_me, 3, discord.ButtonStyle.secondary),
            ('Luck %' if self.draft['mode'] in ('good', 'bad') else 'Extra dice' if self.draft['mode'] in EXTRA_MODES else
             'Enter value', 'value', self.choose_value, 3, discord.ButtonStyle.secondary),
            ('Duration', 'duration', self.choose_duration, 3, discord.ButtonStyle.secondary),
            ('Apply', 'apply', self.apply, 4, discord.ButtonStyle.success),
            ('Active settings', 'active_page', self.show_active, 4, discord.ButtonStyle.primary),
            ('Help', 'help', self.help, 4, discord.ButtonStyle.secondary),
            ('Custom rolls', 'customs', self.show_customs, 4, discord.ButtonStyle.secondary),
        ]:
            self.add_button(label, key, handler, row, style)
        if is_custom_scope(self.draft['scope']):
            self.add_button('Standard rolls', 'standard', self.standard_rolls, 4)

    def roll_options(self):
        if not is_custom_scope(self.draft['scope']): return ROLLS
        template = self.bot.customs.rig_template(self.draft['scope'])
        return {self.draft['scope']: (f"/{template['name']} · Whole character", 'Luck or extra dice on all applicable rolls.'),
                **{e['id']: (f"/{template['name']} · {e['name']}",
                             short(e.get('dice') or 'Weighted outcomes; Force has named presets.'))
                   for e in template['published']['entries']}}

    def custom_entry(self):
        if not is_custom_scope(self.draft['scope']) or self.draft['part'] is None: return None
        template = self.bot.customs.rig_template(self.draft['scope'])
        return next((e for e in template['published']['entries'] if e['id'] == self.draft['part']), None)

    def build_custom_items(self):
        records = self.bot.customs.list(published=True, kind='roll')
        self.custom_page = min(self.custom_page, max(0, (len(records) - 1) // 25))
        self.custom_count = len(records)
        options = []
        for record in records[self.custom_page * 25:self.custom_page * 25 + 25]:
            guild = self.bot.get_guild(int(record['guild']))
            server = guild.name if guild else f"Server {record['guild']}"
            options.append(discord.SelectOption(label=short(f"/{record['name']} · {record['published']['title']}"),
                           value=f"custom:{record['id']}", description=short(server)))
        self.add_item(PanelSelect(self, 'custom', options or [discord.SelectOption(label='No published character commands', value='none')],
                                 row=0, placeholder='Choose a custom character command', disabled=not options))
        self.add_button('Previous', 'custom_prev', self.custom_previous, 1, disabled=self.custom_page == 0)
        self.add_button('Next', 'custom_next', self.custom_next, 1, disabled=(self.custom_page + 1) * 25 >= len(records))
        self.add_button('Standard rolls', 'standard', self.standard_rolls, 1)
        self.add_button('Active settings', 'active_page', self.show_active, 1)

    async def show_customs(self, interaction):
        self.page, self.custom_page = 'customs', 0
        self.note = 'Choose a server’s published character command. Player targeting stays the same.'
        await self.update(interaction)

    async def custom_previous(self, interaction):
        self.custom_page = max(0, self.custom_page - 1)
        await self.update(interaction)

    async def custom_next(self, interaction):
        self.custom_page += 1
        await self.update(interaction)

    async def standard_rolls(self, interaction):
        self.draft.update(scope='roll', part=None, mode='force', value=None)
        self.roll, self.page = 'roll', 'edit'
        self.note = 'Standard rolls selected. Choose /roll or an Avatar part.'
        await self.update(interaction)

    def allowed_modes(self):
        if is_custom_scope(self.draft['scope']):
            if self.draft['part'] is None: return PASSIVE_MODES
            entry = self.custom_entry()
            return tuple(m for m in MODES if m != 'face') if entry and step_plan(entry).numeric else ('face', *EXTRA_MODES)
        return PASSIVE_MODES if self.roll in ('avatar', 'stats') else tuple(
            mode for mode in MODES if mode != 'face' or self.roll == 'roll')

    def quick_choices(self):
        if self.draft['mode'] in ('good', 'bad'):
            return [(f'{percent}% strength', percent) for percent in (1, 25, 50, 75, 100)]
        if self.draft['mode'] in EXTRA_MODES:
            return [(f'+{extra} extra · {extra + 1} total', extra) for extra in (1, 2, 4, 9, 19)]
        if is_custom_scope(self.draft['scope']):
            entry = self.custom_entry()
            if entry and entry['type'] in OUTCOME_TYPES and self.draft['mode'] == 'force':
                options, first = [], 1
                for row in entry['outcomes']:
                    options.append((row['name'], first))
                    first += row['weight']
                return options
            return []
        return PRESETS.get(self.roll, []) if self.draft['mode'] == 'force' else []

    def add_button(self, label, key, handler, row, style=discord.ButtonStyle.secondary, *, disabled=False):
        item = discord.ui.Button(label=label, custom_id=self.component_id(key), row=row, style=style, disabled=disabled)
        async def callback(interaction):
            if await self.authorize(interaction):
                await handler(interaction)
        item.callback = callback
        self.add_item(item)

    def active_slice(self, active):
        rules = [r for r in active['rules'] if not self.player_only or r['target'] == self.draft['target']]
        self.active_page = min(self.active_page, max(0, (len(rules) - 1) // PAGE_SIZE))
        if not any(r['id'] == self.selected_id for r in rules):
            self.selected_id = None
        return rules, rules[self.active_page * PAGE_SIZE:(self.active_page + 1) * PAGE_SIZE]

    def build_active_items(self):
        active = self.bot.engine.control_status(self.bot.engine.owner_id, is_dm=True)
        rules, shown = self.active_slice(active)
        options = [discord.SelectOption(label=short(f"#{r['id']} · {roll_label(r, self.bot.customs)} · {change_label(r, self.bot.customs)}"),
                   value=str(r['id']), description=f"Player {r['target'] or 'anyone (legacy)'}",
                   default=r['id'] == self.selected_id) for r in shown]
        self.add_item(PanelSelect(self, 'active', options or [discord.SelectOption(label='No active settings', value='none')],
                      row=0, placeholder='Choose a setting to edit or remove', disabled=not shown))
        for label, key, handler, row, style, disabled in [
            ('Edit selected', 'edit', self.edit_selected, 1, discord.ButtonStyle.primary, self.selected_id is None),
            ('Remove selected', 'remove', self.remove_selected, 1, discord.ButtonStyle.danger, self.selected_id is None),
            ('Clear player', 'clear_player', self.clear_player, 1, discord.ButtonStyle.danger, False),
            ('Clear all', 'off', self.turn_off, 1, discord.ButtonStyle.danger, not active['rules']),
            ('Previous', 'prev', self.previous_page, 2, discord.ButtonStyle.secondary, self.active_page == 0),
            ('Next', 'next', self.next_page, 2, discord.ButtonStyle.secondary, (self.active_page + 1) * PAGE_SIZE >= len(rules)),
            ('Show all players' if self.player_only else 'Only chosen player', 'filter', self.toggle_filter, 2, discord.ButtonStyle.secondary, False),
            ('Back to draft', 'back', self.back_to_draft, 3, discord.ButtonStyle.secondary, False),
            ('Refresh', 'refresh', self.refresh, 3, discord.ButtonStyle.secondary, False),
            ('Help', 'help', self.help, 3, discord.ButtonStyle.secondary, False),
        ]:
            self.add_button(label, key, handler, row, style, disabled=disabled)

    def embed(self):
        active = self.bot.engine.control_status(self.bot.engine.owner_id, is_dm=True)
        if self.page == 'customs':
            embed = discord.Embed(title='🔒 Custom character settings', description=self.note, colour=0x8B5CF6)
            embed.add_field(name='Named commands', value='Choose a template, then its whole character or an individual named roll.', inline=False)
            embed.set_footer(text=f'{self.custom_count} templates • Page {self.custom_page + 1} • Creator only')
            return embed
        if self.page == 'active':
            rules, shown = self.active_slice(active)
            embed = discord.Embed(title=f"🔒 Active settings · {len(active['rules'])}", description=self.note, colour=0x5865F2)
            embed.add_field(name='Chosen player', value=f"`{self.draft['target']}` · Clear player uses this ID", inline=False)
            for rule in shown:
                embed.add_field(name=f"#{rule['id']} · {roll_label(rule, self.bot.customs)}", value=safe(
                    f"{change_label(rule, self.bot.customs)}\nPlayer {rule['target'] or 'anyone (legacy)'}"), inline=False)
            if not shown:
                embed.add_field(name='Status', value='No active settings in this view.', inline=False)
            if active['last']:
                embed.add_field(name='Last affected roll', value=next(chunks(safe(active['last']), 1000)), inline=False)
            embed.set_footer(text=f"Page {self.active_page + 1}/{max(1, (len(rules) + PAGE_SIZE - 1) // PAGE_SIZE)} • "
                             f"{'Chosen player' if self.player_only else 'All players'} • Private to you")
            return embed
        embed = discord.Embed(title='🔒 Control', description=self.note, colour=0x5865F2)
        target = self.draft['target']
        embed.add_field(name='Player', value=f"{'You' if target == str(self.bot.engine.owner_id) else 'Player'} · `{target}`", inline=False)
        embed.add_field(name='Roll', value=self.roll_options()[self.roll][0], inline=True)
        embed.add_field(name='Change', value=safe(change_value(self.draft, self.bot.customs)), inline=True)
        embed.add_field(name='Duration', value=duration_label(rig_uses(self.draft['mode'], self.draft.get('uses', 'default'))),
                        inline=True)
        player_rules = [r for r in active['rules'] if r['target'] == target]
        embed.set_footer(text=f"Draft until Apply · {len(active['rules'])} active / {len(player_rules)} for this player · "
                              'Details in Active settings')
        return embed

    async def update(self, interaction):
        self.build_items()
        if interaction.response.is_done():
            await self.message.edit(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.edit_message(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)

    async def choose_target(self, interaction):
        await interaction.response.send_modal(PanelInput(self, 'target'))

    async def choose_value(self, interaction):
        await interaction.response.send_modal(PanelInput(self, 'value'))

    async def choose_duration(self, interaction):
        await interaction.response.send_modal(PanelInput(self, 'duration'))

    async def target_me(self, interaction):
        self.draft['target'] = str(self.bot.engine.owner_id)
        self.note = 'Targeting you · Press Apply.'
        await self.update(interaction)

    async def apply(self, interaction):
        await interaction.response.defer()
        try:
            active = self.bot.engine.configure(interaction.user.id, is_dm=interaction.guild_id is None,
                                              request_id=interaction.id, **self.draft)
        except RollError as exc:
            await private_reply(interaction, '⚠️ ' + safe(exc))
            return
        self.note = f"✅ Saved · {len(active['rules'])} active."
        await self.update(interaction)

    async def turn_off(self, interaction):
        await interaction.response.defer()
        self.bot.engine.configure(interaction.user.id, is_dm=interaction.guild_id is None,
                                  mode='off', request_id=interaction.id)
        self.selected_id = None
        self.note = '✅ All settings cleared.'
        await self.update(interaction)

    async def refresh(self, interaction):
        self.note = 'Updated just now.'
        await self.update(interaction)

    async def show_active(self, interaction):
        self.page, self.active_page, self.player_only = 'active', 0, False
        self.note = 'Select a setting to edit or remove.'
        await self.update(interaction)

    async def back_to_draft(self, interaction):
        self.page = 'edit'
        self.note = 'Draft kept · Press Apply to save.'
        await self.update(interaction)

    async def toggle_filter(self, interaction):
        self.player_only = not self.player_only
        self.active_page = 0
        await self.update(interaction)

    async def previous_page(self, interaction):
        self.active_page = max(0, self.active_page - 1)
        await self.update(interaction)

    async def next_page(self, interaction):
        self.active_page += 1
        await self.update(interaction)

    async def edit_selected(self, interaction):
        active = self.bot.engine.control_status(interaction.user.id, is_dm=interaction.guild_id is None)
        rule = next((r for r in active['rules'] if r['id'] == self.selected_id), None)
        if rule is None:
            await private_reply(interaction, 'That setting has already been used or removed. Press Refresh.')
            return
        if rule['scope'] == 'all' or rule['part'] == 'stats' and category(rule) not in ('luck', 'extra'):
            await private_reply(interaction, 'This is a legacy setting. Remove it and create an explicit /roll or Avatar setting.')
            return
        self.draft = {key: rule[key] for key in ('mode', 'value', 'target', 'scope', 'part')}
        self.draft['uses'] = rule['remaining']
        self.draft['target'] = self.draft['target'] or str(self.bot.engine.owner_id)
        self.roll = self.draft['part'] or self.draft['scope']
        self.page = 'edit'
        self.note = f"Editing #{rule['id']} · Press Apply to save."
        await self.update(interaction)

    async def remove_selected(self, interaction):
        await interaction.response.defer()
        if self.selected_id is not None:
            self.bot.engine.remove_control(interaction.user.id, is_dm=interaction.guild_id is None,
                                           rule_id=self.selected_id, request_id=interaction.id)
        self.selected_id = None
        self.note = '✅ setting removed.'
        await self.update(interaction)

    async def clear_player(self, interaction):
        await interaction.response.defer()
        self.bot.engine.remove_control(interaction.user.id, is_dm=interaction.guild_id is None,
                                       target=self.draft['target'], request_id=interaction.id)
        self.note = f"✅ Cleared settings for player {self.draft['target']}. Other players' settings were kept."
        await self.update(interaction)

    async def help(self, interaction):
        for part in chunks(HELP, 1900):
            await private_reply(interaction, part)

    async def refresh_message(self):
        if self.message is not None and not self.is_finished():
            self.build_items()
            await self.message.edit(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)

    async def expire(self):
        self.stop()
        for item in self.children:
            item.disabled = True
        self.note = 'Panel closed. DM !control to reopen it. Active settings are kept.'
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        await self.expire()

    async def on_error(self, interaction, error, item):
        log.warning('Private panel action failed: %s.', type(error).__name__)
        await private_reply(interaction, 'Could not update the setting. Press Refresh or DM !control again.')
