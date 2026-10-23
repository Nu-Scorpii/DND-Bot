"""Permission-checked server workshop. Every form saves a versioned draft."""

import copy
import logging
import re
import discord
from .custom import MAX_ENTRIES, OUTCOME_TYPES, allowed, manager, condition, condition_text, new_field, odds, odds_text, percentage, roll_definition
from .custom_ui import SessionView, Choice, card, character_embeds, ICONS, NO_MENTIONS
from .dice import RNG
from .display import safe, chunks, length, short
from .errors import RollError, require
from .wallets import moderator
from .wallets import text
from .wallet_ui import reply, shown

log = logging.getLogger(__name__)
HELP = ('**Create your own command**\nChoose New command, pick Character rolls, FAQ, or Shop, and give it a slash name. '
        'Existing names such as /avatar and /roll are protected.\n\n'
        '**Character rolls**\nAdd named Dice groups (Origin: 1d5, Stats: 6d20, Power: 1d20). '
        'Add outcome → Named probabilities lets you enter names and chances, such as Uchiha | 5%. '
        'Chances must total exactly 100%; players see only the selected name. '
        'Add outcome → Class + subclasses creates one class roll and a separate subclass table for each class. '
        'Enter Class | chance for classes, then Class | Subclass | chance for each subclass. '
        'Each class and each of its subclass tables must total 100%. Only the matching subclass table rolls. '
        'Relative weights is an alternative that shows the name and numeric draw: 1 and 3 mean 25% and 75%. '
        'Positive decimals with up to four decimal places work. '
        'Use Condition for an earlier roll: Bending >= 61 or Nation == Fire. '
        'Comparisons use the dice total or exact named outcome. A skipped source also skips its dependents.\n\n'
        '**FAQ**\nAdd questions and answers; players browse with a dropdown.\n\n'
        '**Shop**\nAdd items, whole-number prices, and descriptions. Choose the server Wallet. Stock is unlimited. '
        'Players review and confirm purchases from their own balance and see them in My items. '
        'Only moderators award/remove money or record player transfers.\n\n'
        '**Draft → Preview → Publish**\nForm saves update the draft; Publish makes it live as its own slash command. '
        '/custom is also a catalogue and fallback. Unpublish before renaming. Archive is reversible with Restore. '
        'Select a command again to refresh after another editor makes changes. Preview draws normal dice privately '
        'without changing saved results.\n\n'
        '**Limits**\n20 command slots per server, 20 entries per command, 200 base dice per character, '
        '25 outcomes per odds table. Normal dice expressions support modifiers and custom faces. '
        'Server owner/Administrator/Manage Server can edit characters; server moderators can edit FAQs and shops.')


def class_entries(name, classes, subclass_name, subclasses, existing):
    """Build conditional named rolls with independent odds for every class."""
    name = text(name, 40, 'class roll name')
    require(not any(char in name for char in '<>='), 'Class roll names cannot contain <, >, or =.')
    subclass_name = text(subclass_name, 40, 'subclass label')
    class_outcomes = odds(classes, percentages=True)
    require(len(existing) + len(class_outcomes) + 1 <= MAX_ENTRIES,
            'This class setup needs one entry per class plus the class roll. A command holds at most 20 entries.')
    require(all('|' not in row['name'] for row in class_outcomes),
            'Class names in this form cannot contain |.')
    grouped = {row['name'].casefold(): [] for row in class_outcomes}
    require(isinstance(subclasses, str) and len(subclasses) <= 3500,
            'Enter up to 3,500 characters of subclasses.')
    for line in subclasses.splitlines():
        if not line.strip(): continue
        parts = [part.strip() for part in line.split('|')]
        require(len(parts) == 3, 'Use Class | Subclass | chance on each subclass line.')
        require(parts[0].casefold() in grouped,
                f'Unknown class {parts[0][:40]}. Use a class from the first field.')
        grouped[parts[0].casefold()].append(f'{parts[1]} | {parts[2]}')
    require(all(grouped.values()), 'Add at least one subclass for every class.')
    parent = {'id': new_field(), 'name': name, 'type': 'probability', 'outcomes': class_outcomes}
    created = [parent]
    used_names = {entry['name'].casefold() for entry in existing} | {name.casefold()}
    for row in class_outcomes:
        label = row['name']
        outcomes = odds('\n'.join(grouped[label.casefold()]), percentages=True)
        base = f'{subclass_name} · {label}'
        candidate = short(base, 40)
        index = 2
        while candidate.casefold() in used_names:
            suffix = f' {index}'
            candidate = short(base, 40 - length(suffix)) + suffix
            index += 1
        used_names.add(candidate.casefold())
        created.append({'id': new_field(), 'name': candidate, 'display_name': subclass_name,
                        'type': 'probability', 'outcomes': outcomes,
                        'when': {'field': parent['id'], 'op': '==', 'value': label}})
    return created


class BuilderPanel(SessionView):
    def __init__(self, bot, interaction):
        super().__init__(bot, interaction)
        self.context, self.command, self.selected, self.page = interaction, None, None, 'edit'
        self.note = 'Create a command, build its entries, then Publish.'
        self.build()

    def permissions(self, interaction):
        return {'is_manager': manager(interaction), 'is_moderator': moderator(interaction)}

    async def authorize(self, interaction):
        if not await super().authorize(interaction): return False
        kind = self.command['kind'] if self.command and self.page != 'new' else 'faq'
        if not allowed(interaction, kind):
            await reply(interaction, 'Your current server permissions do not allow this editor.')
            return False
        self.context = interaction
        return True

    def build(self):
        self.clear_items()
        self.commands = [c for c in self.bot.customs.list(self.guild_id) if allowed(self.context, c['kind'])]
        if self.page == 'new':
            options = [discord.SelectOption(label=label, value=kind, emoji=ICONS[kind]) for kind, label in
                       [('roll', 'Character rolls'), ('faq', 'FAQ / guide'), ('shop', 'Shop')]
                       if allowed(self.context, kind)]
            self.add_item(Choice(self, options, self.new_kind, placeholder='What do you want to create?'))
            self.add_button('Back', self.back)
            self.add_button('Help', self.help)
            return
        if self.page == 'wallet':
            self.add_item(Choice(self, [discord.SelectOption(label=w['name'], value=str(w['id']), description=w['currency'],
                         default=w['id'] == self.command['draft']['wallet_id']) for w in self.bot.wallets.list(self.guild_id)],
                         self.wallet_selected, placeholder='Choose the shop’s currency wallet'))
            self.add_button('Back', self.back)
            return
        if self.page == 'outcomes':
            self.add_item(Choice(self, [
                discord.SelectOption(label='Named probabilities', value='probability', emoji='✨',
                    description='Exact percentages. Players see only the chosen name.'),
                discord.SelectOption(label='Relative weights', value='table', emoji='🎲',
                    description='Relative odds. Show the name and its numeric draw.'),
                discord.SelectOption(label='Class + subclasses', value='classes', emoji='🛡️',
                    description='Class odds with different subclass odds for each class.')], self.outcome_kind,
                    placeholder='Choose how this outcome works'))
            self.add_button('Back', self.back)
            self.add_button('Help', self.help)
            return
        self.add_item(Choice(self, [discord.SelectOption(label=f"/{c['name']}" + (' · archived' if c['archived'] else ''),
            value=str(c['id']), emoji=ICONS[c['kind']], description=c['draft']['title'][:100],
            default=self.command is not None and c['id'] == self.command['id']) for c in self.commands],
            self.choose_command, placeholder='Choose a command to edit'))
        if self.command is not None:
            entries = self.command['draft']['entries']
            if self.selected not in [e['id'] for e in entries]: self.selected = entries[0]['id'] if entries else None
            self.add_item(Choice(self, [discord.SelectOption(label=f"{i + 1}. {e['name']}", value=e['id'],
                default=e['id'] == self.selected) for i, e in enumerate(entries)], self.choose_entry,
                row=1, placeholder='Choose a roll, question, or item'))
            kind = self.command['kind']
            self.add_button('Add dice' if kind == 'roll' else 'Add question' if kind == 'faq' else 'Add item', self.add_entry, row=2)
            if kind == 'roll': self.add_button('Add outcome', self.add_table, row=2)
            self.add_button('Edit', self.edit_entry, row=2, disabled=self.selected is None)
            if kind == 'roll': self.add_button('Condition', self.edit_condition, row=2, disabled=self.selected is None)
            self.add_button('Remove entry', self.remove_entry, row=2, disabled=self.selected is None)
            self.add_button('Move up', self.move_up, row=3, disabled=self.selected is None or entries[0]['id'] == self.selected)
            self.add_button('Move down', self.move_down, row=3, disabled=self.selected is None or entries[-1]['id'] == self.selected)
            self.add_button('Name & style', self.settings, row=3)
            self.add_button('Preview', self.preview, row=3)
            if kind == 'shop': self.add_button('Wallet', self.wallet, row=3)
            self.add_button('Publish', self.publish, row=4, style=discord.ButtonStyle.success, disabled=not entries)
            self.add_button('Unpublish', self.unpublish, row=4, disabled=self.command['published'] is None)
            self.add_button('Restore' if self.command['archived'] else 'Archive', self.archive, row=4)
        self.add_button('New command', self.new, row=4, style=discord.ButtonStyle.primary)
        self.add_button('Help', self.help, row=4)

    def embed(self):
        if self.page == 'outcomes':
            embed = discord.Embed(title='✨ Create an outcome type', colour=self.command['draft']['color'],
                description='Name the type, add its possible outcomes, and choose their chances.')
            embed.add_field(name='Named probabilities', value='Uchiha | 5%\nHyuga | 10%\nUzumaki | 15%\nOther clan | 70%', inline=False)
            embed.add_field(name='Players see', value='**Nation**\nWater Tribes\n\nA named result, with no numeric die or total.', inline=False)
            embed.add_field(name='Relative weights', value='An alternative for ratios such as 1 : 3. Includes a numeric draw in the result.', inline=False)
            embed.add_field(name='Class + subclasses', value='Add class chances and a subclass table for each class in one form. Only the chosen class’s subclasses roll.', inline=False)
            embed.set_footer(text='Mix outcomes and dice groups in the same command')
            return embed
        if self.command is None or self.page == 'new':
            embed = discord.Embed(title='✦ Server workshop', colour=0x8B5CF6,
                description='**Build something for your campaign.**\n\n✨ Character rolls with names, odds, and conditions\n'
                            '📖 FAQ pages your players can browse\n🛍️ Shops connected to your server wallets')
            embed.add_field(name='Your workflow', value='Create → Add entries → Preview → Publish', inline=False)
            embed.set_footer(text=f'{len(self.commands)}/20 command slots • Private editor')
            return embed
        doc = self.command['draft']
        embed = card(self.command, doc)
        embed.title = '✦ ' + safe(doc['title'])
        status = 'Archived' if self.command['archived'] else 'Published' if self.command['published'] else 'Draft only'
        if self.command['published'] and self.command['published'] != doc: status += ' · draft changes waiting'
        embed.add_field(name='Command', value=f"`/{self.command['name']}`", inline=True)
        embed.add_field(name='Status', value=status, inline=True)
        embed.add_field(name='Entries', value=f"{len(doc['entries'])}/20", inline=True)
        entry = next((e for e in doc['entries'] if e['id'] == self.selected), None)
        if entry:
            if self.command['kind'] == 'roll':
                if entry['type'] == 'dice': details = f"🎲 `{safe(entry['dice'])}`"
                else:
                    total = sum(o['weight'] for o in entry['outcomes'])
                    details = ('✨ Named probabilities · names only\n' if entry['type'] == 'probability' else '🎲 Relative weights\n')
                    details += '\n'.join(f"{safe(o['name'])} · **{percentage(o['weight'], total) if entry['type'] == 'probability' else format(100 * o['weight'] / total, '.6g')}%**" for o in entry['outcomes'])
                when = condition_text(entry, doc['entries'])
                if when: details += '\nOnly when: ' + safe(when)
            elif self.command['kind'] == 'faq': details = safe(entry['answer'])
            else: details = f"Price: **{entry['price']:,}**\n{safe(entry['description'])}"
            embed.add_field(name=shown(entry['name']), value=next(chunks(details, 1000), 'No description.'), inline=False)
        if self.command['kind'] == 'shop':
            wallet = self.bot.wallets.get(self.guild_id, doc['wallet_id'])
            embed.add_field(name='Shop wallet', value=f"{safe(wallet['name'])} · {safe(wallet['currency'])}", inline=False)
        embed.add_field(name='Workshop', value=safe(self.note)[:700], inline=False)
        embed.set_footer(text=f"Draft v{self.command['revision']} • Forms save the draft • Publish updates players’ commands")
        return embed

    async def choose_command(self, interaction, value):
        require(value.isdecimal(), 'Choose a command.')
        command = self.bot.customs.get(self.guild_id, int(value))
        require(allowed(interaction, command['kind']), 'Your permissions do not allow editing this command type.')
        self.command, self.selected = command, None
        self.note = 'Draft loaded. Changes stay private until Publish.'
        await self.update(interaction)

    async def choose_entry(self, interaction, value):
        require(any(e['id'] == value for e in self.command['draft']['entries']), 'Choose a listed entry.')
        self.selected = value
        await self.update(interaction)

    async def new(self, interaction):
        self.page = 'new'
        await self.update(interaction)

    async def new_kind(self, interaction, value):
        require(value in ('roll', 'faq', 'shop') and allowed(interaction, value), 'Choose a command type you can manage.')
        await interaction.response.send_modal(BuilderInput(self, 'new', command_kind=value))

    async def back(self, interaction):
        self.page = 'edit'
        # A moderator returning from New cannot reuse an old character editor after losing Manage Server.
        if self.command and not allowed(interaction, self.command['kind']): self.command = None
        await self.update(interaction)

    async def add_entry(self, interaction):
        await interaction.response.send_modal(BuilderInput(self, 'entry', entry_type='dice'))

    async def add_table(self, interaction):
        self.page = 'outcomes'
        await self.update(interaction)

    async def outcome_kind(self, interaction, value):
        require(value in (*OUTCOME_TYPES, 'classes') and self.command['kind'] == 'roll', 'Choose a valid outcome type.')
        await interaction.response.send_modal(BuilderInput(self, 'classes' if value == 'classes' else 'entry', entry_type=value))

    async def edit_entry(self, interaction):
        await interaction.response.send_modal(BuilderInput(self, 'edit'))

    async def edit_condition(self, interaction):
        await interaction.response.send_modal(BuilderInput(self, 'condition'))

    async def settings(self, interaction):
        await interaction.response.send_modal(BuilderInput(self, 'settings'))

    async def save_data(self, interaction, data, *, name=None):
        self.command = self.bot.customs.save(interaction.user.id, self.guild_id, self.command['id'], self.command['revision'],
            data, name=name, **self.permissions(interaction))
        self.page = 'edit'
        self.note = '✓ Draft saved. Preview or Publish when ready.'
        await self.update(interaction)

    async def remove_entry(self, interaction):
        await interaction.response.defer()
        data = copy.deepcopy(self.command['draft'])
        data['entries'] = [e for e in data['entries'] if e['id'] != self.selected]
        await self.save_data(interaction, data)

    async def move(self, interaction, direction):
        await interaction.response.defer()
        data = copy.deepcopy(self.command['draft'])
        index = next(i for i, e in enumerate(data['entries']) if e['id'] == self.selected)
        other = index + direction
        require(0 <= other < len(data['entries']), 'This entry is already at the end.')
        data['entries'][index], data['entries'][other] = data['entries'][other], data['entries'][index]
        await self.save_data(interaction, data)

    async def move_up(self, interaction): await self.move(interaction, -1)
    async def move_down(self, interaction): await self.move(interaction, 1)

    async def wallet(self, interaction):
        self.page = 'wallet'
        await self.update(interaction)

    async def wallet_selected(self, interaction, value):
        require(self.command['kind'] == 'shop' and value.isdecimal(), 'Choose a wallet for a shop.')
        await interaction.response.defer()
        data = copy.deepcopy(self.command['draft'])
        data['wallet_id'] = self.bot.wallets.get(self.guild_id, int(value))['id']
        self.page = 'edit'
        await self.save_data(interaction, data)

    async def change_visibility(self, interaction, action):
        await interaction.response.defer()
        self.command = self.bot.customs.visibility(interaction.user.id, self.guild_id, self.command['id'],
            self.command['revision'], action, **self.permissions(interaction))
        self.note = {'publish': '✓ Published. Players can use its command or /custom.', 'unpublish': 'Command unpublished; draft kept.',
                     'archive': 'Command archived. Restore to edit it again.', 'restore': 'Draft restored. Publish to make it available.'}[action]
        try:
            await self.bot.sync_custom_guild(interaction.guild, force=True)
        except discord.HTTPException:
            self.note += ' Slash-menu sync is pending. /custom reflects the saved state; Publish or Unpublish again to retry.'
        await self.update(interaction)

    async def publish(self, interaction): await self.change_visibility(interaction, 'publish')
    async def unpublish(self, interaction): await self.change_visibility(interaction, 'unpublish')
    async def archive(self, interaction): await self.change_visibility(interaction, 'restore' if self.command['archived'] else 'archive')

    async def preview(self, interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        doc = self.command['draft']
        if self.command['kind'] == 'roll':
            result = roll_definition(doc, lambda entry, plan: plan.roll(RNG))
            embeds = character_embeds(result, preview=True)
        else:
            embed = card(self.command, doc)
            for entry in doc['entries'][:3]:
                body = entry['answer'] if self.command['kind'] == 'faq' else f"{entry['price']:,} · {entry['description']}"
                embed.add_field(name=shown(entry['name']), value=next(chunks(safe(body), 900), 'No description.'), inline=False)
            embed.set_footer(text='Draft preview • Nothing published or purchased')
            embeds = [embed]
        await interaction.edit_original_response(embed=embeds[0], allowed_mentions=NO_MENTIONS)
        for embed in embeds[1:]: await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def help(self, interaction):
        for part in chunks(HELP, 1900): await reply(interaction, part)


class BuilderInput(discord.ui.Modal):
    def __init__(self, panel, action, *, command_kind=None, entry_type=None):
        self.panel, self.action = panel, action
        self.identity = panel.command['id'] if panel.command else None
        self.revision = panel.command['revision'] if panel.command else None
        self.selected = panel.selected
        self.kind = command_kind or panel.command['kind']
        self.entry = next((e for e in panel.command['draft']['entries'] if e['id'] == panel.selected), None) if panel.command else None
        self.entry_type = self.entry.get('type') if action == 'edit' and self.entry else entry_type
        super().__init__(title={'new': 'Create a command', 'settings': 'Command name & style', 'condition': 'Conditional roll',
                               'edit': 'Edit entry', 'entry': 'Add entry', 'classes': 'Add classes & subclasses'}[action], timeout=300)
        self.fields = {}
        if action in ('new', 'settings'):
            doc = panel.command['draft'] if action == 'settings' else {}
            self.field('slug', 'Slash command name', 32, default=panel.command['name'] if action == 'settings' else '', placeholder='hero, market, rules')
            self.field('title', 'Display title', 80, default=doc.get('title', ''), placeholder='Your campaign’s character creator')
            self.field('description', 'Introduction (optional)', 700, default=doc.get('description', ''), required=False, long=True)
            if action == 'settings': self.field('color', 'Accent colour (six-digit hex)', 7, default=f"{doc['color']:06X}", placeholder='8B5CF6')
        elif action == 'condition':
            self.field('when', 'Only roll when… (blank = always)', 200, default=condition_text(self.entry, panel.command['draft']['entries']),
                       placeholder='Bending >= 61 or Nation == Fire', required=False)
        elif action == 'classes':
            self.field('name', 'Class roll name', 40, default='Class')
            self.field('outcomes', 'Class | chance % (total 100%)', 3500,
                       placeholder='Fighter | 60%\nMage | 40%', long=True)
            self.field('subclass_name', 'Subclass player label', 40, default='Subclass')
            self.field('subclasses', 'Class | Subclass | chance %', 3500,
                       placeholder='Fighter | Champion | 70%\nFighter | Guardian | 30%\nMage | Evoker | 100%', long=True)
        else:
            old = self.entry if action == 'edit' else {}
            self.field('name', 'Question' if self.kind == 'faq' else 'Entry name (editor)' if old.get('display_name') else 'Outcome type name' if self.entry_type == 'probability' else 'Roll name' if self.kind == 'roll' else 'Item name',
                       60 if self.kind == 'faq' else 40, default=old.get('name', ''))
            if old.get('display_name'):
                self.field('display_name', 'Player label', 40, default=old['display_name'])
            if self.kind == 'roll':
                if self.entry_type in OUTCOME_TYPES:
                    exact = self.entry_type == 'probability'
                    self.field('outcomes', 'Outcome | chance % (total must be 100%)' if exact else 'One outcome | weight per line',
                               3500, default=odds_text(old.get('outcomes', []), percentages=exact),
                               placeholder='Fire | 25%\nWater | 25%\nEarth | 25%\nNomad | 25%' if exact else 'Common | 3\nRare | 1', long=True)
                else:
                    self.field('dice', 'Dice expression', 500, default=old.get('dice', ''), placeholder='1d5, 6d20, or 1d20+2')
                self.field('when', 'Only when… (optional)', 200, default=condition_text(old, panel.command['draft']['entries']),
                           placeholder='Bending >= 61', required=False)
            elif self.kind == 'faq': self.field('answer', 'Answer', 1500, default=old.get('answer', ''), long=True)
            else:
                self.field('price', 'Price per item (whole number)', 13, default=str(old.get('price', 0)))
                self.field('description', 'Item description (optional)', 700, default=old.get('description', ''), required=False, long=True)

    def field(self, key, label, limit, *, default='', placeholder=None, required=True, long=False):
        item = discord.ui.TextInput(default=default or None, placeholder=placeholder, required=required, max_length=limit,
                                   style=discord.TextStyle.paragraph if long else discord.TextStyle.short)
        self.fields[key] = item
        self.add_item(discord.ui.Label(text=label, component=item))

    async def interaction_check(self, interaction):
        return await self.panel.authorize(interaction)

    async def on_submit(self, interaction):
        if not await self.panel.authorize(interaction): return
        try:
            require(allowed(interaction, self.kind), 'Your server permissions no longer allow this command type.')
            current_id = self.panel.command['id'] if self.panel.command else None
            current_rev = self.panel.command['revision'] if self.panel.command else None
            require((current_id, current_rev) == (self.identity, self.revision), 'The editor changed. Open a fresh form.')
            require(self.action not in ('edit', 'condition') or self.panel.selected == self.selected, 'The selected entry changed. Open a fresh form.')
            values = {k: field.value.strip() for k, field in self.fields.items()}
            await interaction.response.defer()
            if self.action == 'new':
                self.panel.command = self.panel.bot.customs.create(interaction.user.id, self.panel.guild_id, values['slug'], self.kind,
                    values['title'], values['description'], **self.panel.permissions(interaction))
                self.panel.page, self.panel.selected = 'edit', None
                self.panel.note = '✓ New draft. Add entries to get started.'
                await self.panel.update(interaction)
                return
            data = copy.deepcopy(self.panel.command['draft'])
            if self.action == 'settings':
                color = values['color'].removeprefix('#')
                require(re.fullmatch(r'[a-fA-F0-9]{6}', color), 'Use a six-digit colour, such as 8B5CF6.')
                data.update(title=values['title'], description=values['description'], color=int(color, 16))
            elif self.action == 'classes':
                require(self.kind == 'roll', 'Classes belong to character rolls.')
                created = class_entries(values['name'], values['outcomes'], values['subclass_name'],
                                        values['subclasses'], data['entries'])
                data['entries'].extend(created)
                self.panel.selected = created[0]['id']
            else:
                position = next((i for i, e in enumerate(data['entries']) if e['id'] == self.selected), len(data['entries'])) if self.action in ('edit', 'condition') else len(data['entries'])
                entry = copy.deepcopy(data['entries'][position]) if self.action in ('edit', 'condition') else {'id': new_field()}
                if self.action != 'condition':
                    entry['name'] = values['name']
                    if self.kind == 'roll':
                        entry['type'] = self.entry_type
                        if self.entry_type in OUTCOME_TYPES: entry['outcomes'] = odds(values['outcomes'], percentages=self.entry_type == 'probability')
                        else: entry['dice'] = values['dice']
                        if 'display_name' in values: entry['display_name'] = values['display_name']
                    elif self.kind == 'faq': entry['answer'] = values['answer']
                    else:
                        require(values['price'].isdecimal(), 'Enter a non-negative whole price.')
                        entry.update(price=int(values['price']), description=values['description'])
                if self.kind == 'roll':
                    check = condition(values['when'], data['entries'], position)
                    entry.pop('when', None)
                    if check: entry['when'] = check
                if position == len(data['entries']): data['entries'].append(entry)
                else: data['entries'][position] = entry
                self.panel.selected = entry['id']
            await self.panel.save_data(interaction, data, name=values.get('slug'))
        except RollError as exc: await reply(interaction, '⚠️ ' + safe(exc))

    async def on_error(self, interaction, error):
        log.warning('Builder form failed: %s.', type(error).__name__)
        await reply(interaction, 'Could not save this form. Select the command again to check its current draft.')
