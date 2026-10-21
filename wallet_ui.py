"""Simple player wallet cards; creator naming lives in a separate panel."""

import logging
import secrets
import discord
from .display import safe, length
from .errors import RollError
from .wallets import PAGE_SIZE, MAX_WALLETS

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
TEAL = 0x2DD4BF


async def reply(interaction, content):
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, allowed_mentions=NO_MENTIONS)
    else:
        await interaction.response.send_message(content, ephemeral=True, allowed_mentions=NO_MENTIONS)


def shown(value, budget=120):
    result = safe(' '.join(str(value).split()))
    while length(result) > budget:
        result = result[:-1]
    return result.rstrip('\\') or 'Player'


async def member(interaction, player_id):
    guild = interaction.guild
    if guild is None or guild.id != interaction.guild_id:
        raise RollError('Use wallet commands in their server.')
    try:
        player = await guild.fetch_member(player_id)
    except discord.NotFound:
        raise RollError('That player is no longer in this server. Choose a current member.')
    except discord.HTTPException:
        raise RollError('Could not check that player. Try again in a moment.')
    if player.bot:
        raise RollError('Choose a player account, not a bot.')
    return player


class WalletChoice(discord.ui.Select):
    def __init__(self, panel, wallets):
        self.panel = panel
        super().__init__(custom_id=panel.key('wallet'), placeholder='Choose your wallet', row=0,
            options=[discord.SelectOption(label=w['name'], value=str(w['id']), emoji='🪙',
                      description=f"Currency: {w['currency']}", default=w['id'] == panel.wallet_id) for w in wallets])

    async def callback(self, interaction):
        if not await self.panel.authorize(interaction):
            return
        await interaction.response.defer()
        try:
            value = self.values[0]
            if not value.isdecimal() or len(value) > 19:
                raise RollError('Choose a wallet from this server.')
            wallet = self.panel.bot.wallets.get(self.panel.guild_id, int(value))
            self.panel.wallet_id, self.panel.page = wallet['id'], 0
            await self.panel.update(interaction)
        except RollError as exc:
            await reply(interaction, '⚠️ ' + safe(exc))


class WalletInput(discord.ui.Modal):
    def __init__(self, panel, kind):
        if kind not in ('new', 'edit'):
            raise RollError('Use /wallet-add, /wallet-remove, or /wallet-transfer to change money.')
        self.panel, self.kind, self.wallet_id = panel, kind, panel.wallet_id
        super().__init__(title='New wallet' if kind == 'new' else 'Edit wallet', timeout=180)
        wallet = panel.bot.wallets.get(panel.guild_id, self.wallet_id) if kind == 'edit' else None
        self.first = discord.ui.TextInput(default=wallet['name'] if wallet else None,
                                         placeholder='Avatar campaign, UA, savings…', min_length=1, max_length=32)
        self.second = discord.ui.TextInput(default=wallet['currency'] if wallet else 'coins',
                                          placeholder='coins, credits, gold…', min_length=1, max_length=20)
        self.add_item(discord.ui.Label(text='Wallet name', component=self.first))
        self.add_item(discord.ui.Label(text='Currency label', component=self.second))

    async def interaction_check(self, interaction):
        return await self.panel.authorize(interaction, creator=True)

    async def on_submit(self, interaction):
        if not await self.interaction_check(interaction):
            return
        try:
            if self.kind == 'edit' and self.wallet_id != self.panel.wallet_id:
                raise RollError('The selected wallet changed. Open a new form.')
            await interaction.response.defer()
            result = self.panel.bot.wallets.save(interaction.user.id, self.panel.guild_id, self.first.value,
                self.second.value, wallet_id=self.wallet_id if self.kind == 'edit' else None, request_id=interaction.id)
            self.panel.wallet_id, self.panel.page = result['wallet']['id'], 0
            self.panel.note = '✅ Wallet saved. Balances and history are kept.'
            await self.panel.update(interaction)
        except RollError as exc:
            await reply(interaction, '⚠️ ' + safe(exc))

    async def on_error(self, interaction, error):
        log.warning('Wallet naming form failed: %s.', type(error).__name__)
        await reply(interaction, 'Could not save the wallet. Open /wallet-manage to check before trying again.')


class WalletPanel(discord.ui.View):
    def __init__(self, bot, interaction, *, wallet=None, manage=False):
        super().__init__(timeout=600)
        if manage and interaction.user.id != bot.engine.owner_id:
            raise RollError('Only the bot creator can name wallets.')
        self.bot, self.message = bot, None
        self.author_id, self.guild_id = interaction.user.id, interaction.guild_id
        self.player, self.guild_name = interaction.user, getattr(interaction.guild, 'name', 'This server')
        self.manage, self.history, self.page = manage, False, 0
        self.nonce = secrets.token_hex(6)
        self.wallet_id = bot.wallets.resolve(self.guild_id, wallet, viewing=True)['id']
        self.note = 'Choose a wallet. History shows your rewards, spending, and deals.'
        self.build_items()

    def key(self, kind):
        return f'wallet:{self.nonce}:{kind}'

    async def authorize(self, interaction, *, creator=False):
        if interaction.user.id != self.author_id or interaction.guild_id != self.guild_id:
            await reply(interaction, 'This is someone else’s panel. Use /wallet in your server.')
            return False
        if self.is_finished():
            await reply(interaction, 'This panel has closed. Your money is saved; use /wallet again.')
            return False
        if creator and (not self.manage or interaction.user.id != self.bot.engine.owner_id):
            await reply(interaction, 'Only the bot creator can name wallets, using /wallet-manage.')
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.authorize(interaction)

    def snapshot(self):
        data = self.bot.wallets.snapshot(self.guild_id, self.wallet_id, self.author_id,
            page=self.page if self.history else 0, page_size=PAGE_SIZE if self.history else 3)
        self.page = data['page']
        return data

    def add_button(self, label, kind, handler, *, row=1, emoji=None, disabled=False, creator=False):
        item = discord.ui.Button(label=label, custom_id=self.key(kind), row=row, emoji=emoji,
                                style=discord.ButtonStyle.secondary, disabled=disabled)
        async def callback(interaction):
            if await self.authorize(interaction, creator=creator):
                await handler(interaction)
        item.callback = callback
        self.add_item(item)

    def build_items(self):
        self.clear_items()
        wallets = self.bot.wallets.list(self.guild_id)
        self.add_item(WalletChoice(self, wallets))
        self.add_button('Balance' if self.history else 'History', 'history', self.toggle_history, emoji='📒')
        self.add_button('Refresh', 'refresh', self.refresh, emoji='🔄')
        if self.manage:
            self.add_button('New wallet', 'new', self.new_wallet, row=2, emoji='🪙', creator=True,
                            disabled=len(wallets) >= MAX_WALLETS)
            self.add_button('Edit wallet', 'edit', self.edit_wallet, row=2, creator=True)
        data = self.snapshot()
        if self.history:
            self.add_button('Previous', 'prev', self.previous, row=3, disabled=data['page'] == 0)
            self.add_button('Next', 'next', self.next, row=3, disabled=data['page'] + 1 >= data['pages'])

    def embed(self):
        data = self.snapshot()
        wallet = data['wallet']
        embed = discord.Embed(title='🪙 ' + safe(wallet['name']), colour=TEAL,
                              description=f"**{data['balance']:,} {safe(wallet['currency'])}**\n{self.note}")
        embed.set_author(name='Wallet settings' if self.manage else 'Your wallet')
        embed.add_field(name='Player', value=shown(self.player.display_name), inline=True)
        embed.add_field(name='Server', value=shown(self.guild_name), inline=True)
        for item in data['entries']:
            if item['transfer_id'] is not None:
                label = ('Sent to' if item['delta'] < 0 else 'Received from') + f" player {item['counterparty']}"
                label += f" · Deal #{item['transfer_id']}"
            else:
                label = 'Money added' if item['delta'] > 0 else 'Money removed'
            embed.add_field(name=label, value=f"**{item['delta']:+,}** · {safe(item['reason'])}\n"
                            f"Balance {item['balance_after']:,} · <t:{item['created_at']}:R> · Mod `{item['actor']}`", inline=False)
        if not data['entries']:
            embed.add_field(name='Activity', value='Nothing yet. Rewards and deals will appear here.', inline=False)
        page = f" · Page {data['page'] + 1}/{data['pages']}" if self.history else ''
        embed.set_footer(text='Only you can see this' + page + ' · Refresh for the latest balance')
        return embed

    async def update(self, interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.build_items()
        await self.message.edit(embed=self.embed(), view=self, allowed_mentions=NO_MENTIONS)

    async def new_wallet(self, interaction):
        await interaction.response.send_modal(WalletInput(self, 'new'))

    async def edit_wallet(self, interaction):
        await interaction.response.send_modal(WalletInput(self, 'edit'))

    async def refresh(self, interaction):
        self.note = 'Your latest balance. Ask a server moderator to record rewards or deals.'
        await self.update(interaction)

    async def toggle_history(self, interaction):
        self.history, self.page = not self.history, 0
        await self.update(interaction)

    async def previous(self, interaction):
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    async def next(self, interaction):
        self.page += 1
        await self.update(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                embed = self.embed()
                embed.set_footer(text='Panel closed · Your money is saved · /wallet to reopen')
                await self.message.edit(embed=embed, view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        log.warning('Wallet panel failed: %s.', type(error).__name__)
        await reply(interaction, 'Could not refresh the panel. Use /wallet again.')
