"""Player-facing custom command cards, FAQ navigation, and reviewed shop checkout."""

import logging
import secrets
import discord
from .display import chunks, length, roll_messages, safe
from .errors import RollError, require
from .wallet_ui import member, reply, shown

NO_MENTIONS = discord.AllowedMentions.none()
ICONS = {'roll': '✨', 'faq': '📖', 'shop': '🛍️'}
log = logging.getLogger(__name__)


def card(command, document=None):
    doc = document or command['published']
    embed = discord.Embed(title=f"{ICONS[command['kind']]} {safe(doc['title'])}",
                          description=safe(doc['description']) or None, colour=doc['color'])
    embed.set_footer(text=f"/{command['name']} • Made for this server")
    return embed


def character_embeds(character, name='Player', *, preview=False):
    heading = f"{'🧪' if preview else '✨'} {safe(character['title'])}"
    intro = f"{'Preview' if preview else shown(name)}\n{safe(character['description'])}".strip()
    pages = [discord.Embed(title=heading, description=intro, colour=character['color'])]
    size = length(heading + intro)
    for entry in character['results']:
        if entry.get('type') == 'probability':
            body = f"**{safe(entry['outcome'])}**"
        else:
            bodies = [message.split('\n\n', 1)[1] for message in roll_messages(entry['dice'])]
            body = '\n'.join(bodies)
            if entry['outcome'] is not None:
                body = f"**{safe(entry['outcome'])}**\n" + body
        for index, part in enumerate(chunks(body, 1000)):
            title = safe(entry['name']) + (' · continued' if index else '')
            if size + length(title + part) > 5500 or len(pages[-1].fields) >= 20:
                pages.append(discord.Embed(title=heading + ' · continued', colour=character['color']))
                size = length(pages[-1].title)
            pages[-1].add_field(name=title, value=part, inline=False)
            size += length(title + part)
    if not character['results']:
        pages[0].add_field(name='No rolls matched', value='None of this template’s conditions were met.', inline=False)
    for index, embed in enumerate(pages):
        embed.set_footer(text=('Draft preview • Nothing saved' if preview else 'Character creation') +
                              (f' • {index + 1}/{len(pages)}' if len(pages) > 1 else ''))
    return pages


class SessionView(discord.ui.View):
    def __init__(self, bot, interaction):
        super().__init__(timeout=600)
        self.bot, self.viewer, self.guild_id = bot, interaction.user.id, interaction.guild_id
        self.nonce = secrets.token_hex(6)
        self.message = None

    async def authorize(self, interaction):
        if self.is_finished() or interaction.user.id != self.viewer or interaction.guild_id != self.guild_id:
            await reply(interaction, 'This panel belongs to its opener in this server. Run the command again for your own panel.')
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.authorize(interaction)

    def add_button(self, label, action, row=1, style=discord.ButtonStyle.secondary, *, disabled=False):
        item = discord.ui.Button(label=label, custom_id=f'custom:{self.nonce}:{len(self.children)}',
                                 row=row, style=style, disabled=disabled)
        async def callback(interaction):
            if await self.authorize(interaction):
                try:
                    await action(interaction)
                except RollError as exc:
                    await reply(interaction, '⚠️ ' + safe(exc))
        item.callback = callback
        self.add_item(item)

    async def update(self, interaction):
        self.build()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.edit_message(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)

    async def on_error(self, interaction, error, item):
        log.warning('Custom panel failed: %s.', type(error).__name__)
        await reply(interaction, 'Could not finish this action. Open the command again. Check your wallet before retrying a purchase.')

    async def on_timeout(self):
        for item in self.children: item.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except discord.HTTPException: pass


class Choice(discord.ui.Select):
    def __init__(self, panel, options, handler, *, row=0, placeholder='Choose an entry'):
        self.panel, self.handler = panel, handler
        super().__init__(custom_id=f'choice:{panel.nonce}:{row}', row=row, placeholder=placeholder,
                         options=options or [discord.SelectOption(label='No entries yet', value='none')], disabled=not options)

    async def callback(self, interaction):
        if not await self.panel.authorize(interaction): return
        try: await self.handler(interaction, self.values[0])
        except RollError as exc: await reply(interaction, '⚠️ ' + safe(exc))


class Catalogue(SessionView):
    def __init__(self, bot, interaction):
        super().__init__(bot, interaction)
        self.selected = None
        self.build()

    def build(self):
        self.clear_items()
        self.commands = self.bot.customs.list(self.guild_id, published=True)
        if self.selected not in [c['id'] for c in self.commands]:
            self.selected = self.commands[0]['id'] if self.commands else None
        self.add_item(Choice(self, [discord.SelectOption(label='/' + c['name'], value=str(c['id']), emoji=ICONS[c['kind']],
            description=c['published']['title'][:100], default=c['id'] == self.selected) for c in self.commands], self.choose,
            placeholder='Explore this server’s commands'))
        self.add_button('Open command', self.open, style=discord.ButtonStyle.primary, disabled=self.selected is None)
        self.add_button('Refresh', self.update)

    def embed(self):
        embed = discord.Embed(title='✦ Server collection', description='Character creators, guides, and shops made for this server.', colour=0x8B5CF6)
        current = next((c for c in self.commands if c['id'] == self.selected), None)
        if current:
            body = f"**{safe(current['published']['title'])}**\n{safe(current['published']['description'])}"
            embed.add_field(name=f"{ICONS[current['kind']]} /{current['name']}",
                            value=next(chunks(body, 1000)), inline=False)
        else:
            embed.add_field(name='Ready for your campaign', value='A server owner or moderator can use /builder to publish a command.', inline=False)
        embed.set_footer(text=f'{len(self.commands)} published • Everyone can explore')
        return embed

    async def choose(self, interaction, value):
        require(value.isdecimal() and any(c['id'] == int(value) for c in self.commands), 'Choose a listed command.')
        self.selected = int(value)
        await self.update(interaction)

    async def open(self, interaction):
        await self.bot.run_custom(interaction, identity=self.selected)


class ContentPanel(SessionView):
    def __init__(self, bot, interaction, command):
        super().__init__(bot, interaction)
        self.identity, self.command, self.selected = command['id'], command, None
        self.inventory_page, self.inventory_mode = 0, False
        self.build()

    def build(self):
        self.clear_items()
        self.command = self.bot.customs.get(self.guild_id, self.identity, published=True)
        entries = self.command['published']['entries']
        if self.selected not in [e['id'] for e in entries]: self.selected = entries[0]['id'] if entries else None
        if self.inventory_mode:
            self.add_button('Previous', self.previous, disabled=self.inventory_page == 0)
            self.add_button('Next', self.next)
            self.add_button('Back to shop', self.back)
            return
        self.add_item(Choice(self, [discord.SelectOption(label=e['name'], value=e['id'], default=e['id'] == self.selected)
                                   for e in entries], self.choose, placeholder='Choose a question' if self.command['kind'] == 'faq' else 'Browse items'))
        if self.command['kind'] == 'shop':
            self.add_button('Buy item', self.buy, style=discord.ButtonStyle.success, disabled=self.selected is None)
            self.add_button('My items', self.inventory)
        self.add_button('Refresh', self.update)

    def embed(self):
        embed = card(self.command)
        if self.inventory_mode:
            entries = self.bot.customs.inventory(self.viewer, self.guild_id, self.identity)
            self.inventory_page = min(self.inventory_page, max(0, (len(entries) - 1) // 10))
            for entry in entries[self.inventory_page * 10:self.inventory_page * 10 + 10]:
                embed.add_field(name=shown(entry['name']), value=f"Owned: **{entry['quantity']:,}**", inline=True)
            if not entries: embed.add_field(name='Your bag is empty', value='Purchased items will appear here.', inline=False)
            embed.set_footer(text=f'Your purchases • Page {self.inventory_page + 1}/{max(1, (len(entries) + 9) // 10)}')
            return embed
        entry = next((e for e in self.command['published']['entries'] if e['id'] == self.selected), None)
        if not entry: return embed
        if self.command['kind'] == 'faq':
            for index, part in enumerate(chunks(safe(entry['answer']), 1000)):
                embed.add_field(name=shown(entry['name']) if index == 0 else 'Continued', value=part, inline=False)
        else:
            wallet = self.command['published']['wallet_id']
            snapshot = self.bot.wallets.snapshot(self.guild_id, wallet, self.viewer)
            embed.add_field(name=shown(entry['name']), value=next(chunks(safe(entry['description']), 1000), 'Ready for your next session.'), inline=False)
            currency = safe(snapshot['wallet']['currency'])
            embed.add_field(name='Price each', value=f"**{entry['price']:,} {currency}**")
            embed.add_field(name='Your balance', value=f"**{snapshot['balance']:,} {currency}**")
            embed.set_footer(text=f"{snapshot['wallet']['name']} • Unlimited stock • Review before buying")
        return embed

    async def choose(self, interaction, value):
        require(any(e['id'] == value for e in self.command['published']['entries']), 'Choose a listed entry.')
        self.selected = value
        await self.update(interaction)

    async def buy(self, interaction):
        await interaction.response.send_modal(QuantityInput(self))

    async def inventory(self, interaction):
        self.inventory_mode = True
        await self.update(interaction)

    async def previous(self, interaction):
        self.inventory_page = max(0, self.inventory_page - 1)
        await self.update(interaction)

    async def next(self, interaction):
        self.inventory_page += 1
        await self.update(interaction)

    async def back(self, interaction):
        self.inventory_mode = False
        await self.update(interaction)


class QuantityInput(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title='Choose quantity', timeout=180)
        self.panel, self.item, self.revision = panel, panel.selected, panel.command['live_revision']
        self.quantity = discord.ui.TextInput(default='1', min_length=1, max_length=3)
        self.add_item(discord.ui.Label(text='How many? (1–100)', component=self.quantity))

    async def on_submit(self, interaction):
        if not await self.panel.authorize(interaction): return
        try:
            require(self.panel.selected == self.item, 'The selected item changed. Open Buy item again.')
            raw = self.quantity.value.strip()
            require(raw.isdecimal() and 1 <= int(raw) <= 100, 'Choose 1–100 items.')
            current = self.panel.bot.customs.get(self.panel.guild_id, self.panel.identity, published=True)
            require(current['live_revision'] == self.revision, 'This shop changed. Refresh it before buying.')
            quote = Checkout(self.panel.bot, interaction, current, self.item, int(raw))
            await interaction.response.send_message(embed=quote.embed(), view=quote, ephemeral=True, allowed_mentions=NO_MENTIONS)
            quote.message = await interaction.original_response()
        except RollError as exc: await reply(interaction, '⚠️ ' + safe(exc))

    async def on_error(self, interaction, error):
        log.warning('Shop quantity form failed: %s.', type(error).__name__)
        await reply(interaction, 'Could not open checkout. No purchase has been made.')


class Checkout(SessionView):
    def __init__(self, bot, interaction, command, item_id, quantity):
        super().__init__(bot, interaction)
        self.command, self.item_id, self.quantity = command, item_id, quantity
        self.request_id = interaction.id  # One review card is one durable purchase, even after double clicks.
        self.item = next(e for e in command['published']['entries'] if e['id'] == item_id)
        require(self.item['price'] * quantity <= 1_000_000_000_000, 'This purchase exceeds the currency limit.')
        self.add_button('Confirm purchase', self.confirm, style=discord.ButtonStyle.success)
        self.add_button('Cancel', self.cancel)

    def embed(self):
        wallet = self.bot.wallets.get(self.guild_id, self.command['published']['wallet_id'])
        embed = discord.Embed(title='🧾 Review purchase', colour=self.command['published']['color'],
            description=f"**{self.quantity} × {safe(self.item['name'])}**\n/{self.command['name']}")
        embed.add_field(name='Total cost', value=f"**{self.quantity * self.item['price']:,} {safe(wallet['currency'])}**")
        embed.add_field(name='Pay from', value=safe(wallet['name']))
        embed.set_footer(text='Only your own wallet is charged • Confirm once')
        return embed

    async def confirm(self, interaction):
        await interaction.response.defer()
        await member(interaction, self.viewer)
        if not await self.authorize(interaction): return
        result = self.bot.customs.purchase(self.viewer, self.guild_id, self.command['id'], self.item_id, self.quantity,
                                          self.command['live_revision'], request_id=self.request_id)
        embed = discord.Embed(title='✓ Purchase complete', colour=0x2DD4BF,
            description=f"**{result['quantity']} × {safe(result['item'])}** added to My items.")
        embed.add_field(name='Paid', value=f"{result['total']:,} {safe(result['wallet']['currency'])}")
        embed.add_field(name='Balance', value=f"{result['balance']:,} {safe(result['wallet']['currency'])}")
        embed.set_footer(text='Already saved • No second charge' if result['replayed'] else 'Saved in your wallet history and My items')
        for item in self.children: item.disabled = True
        self.stop()
        try:
            await interaction.edit_original_response(embed=embed, view=self, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            await reply(interaction, 'Your purchase was saved. Check /wallet and My items before buying again.')

    async def cancel(self, interaction):
        for item in self.children: item.disabled = True
        self.stop()
        await interaction.response.edit_message(content='Purchase cancelled. No money spent.', embed=None, view=self)
