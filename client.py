"""Custom dice and Avatar creation, with private creator controls."""

import logging
import asyncio
import discord
from discord import app_commands
from . import __version__
from .dice import prepare
from .controls import COMMAND
from .display import avatar_messages, private_messages, roll_messages, safe, short
from .errors import RollError
from .rigger_ui import RiggerPanel
from .wallets import Wallets, MAX_MONEY, moderator
from .wallet_ui import WalletPanel, member, shown, TEAL
from .custom import allowed
from .custom_ui import Catalogue, ContentPanel, character_embeds
from .builder_ui import BuilderPanel

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


class DiceBot(discord.Client):
    def __init__(self, engine):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.dm_messages = True
        # The creator uses DMs; privileged Message Content intent is unnecessary.
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)
        self.engine = engine
        self.wallets = Wallets(engine.store, engine.owner_id)
        self.customs = engine.customs
        self.custom_sync_locks = {}
        self.tree = app_commands.CommandTree(self)
        self.cleaned_guilds = set()
        self.rigger_panel = None
        self.rigger_message_id = None
        self.tree.on_error = self.command_error

        @self.tree.command(name='roll', description='Roll dice. Add a bonus or penalty in the same expression: 1d100+2 or 1d100-2.')
        @app_commands.guild_only()
        @app_commands.describe(dice='1d100, 1d100+2, or 1d100-2. Any dice size; leave blank for 1d20.')
        async def roll(interaction: discord.Interaction, dice: str | None = None):
            await self.run_roll(interaction, dice=dice)

        @self.tree.command(name='avatar', description='Roll Avatar IQ, six stats, origin, bending strength for benders, or non-bender combat.')
        @app_commands.guild_only()
        async def avatar(interaction: discord.Interaction):
            await self.run_avatar(interaction)

        @self.tree.command(name='wallet', description='Everyone: check your balance and deal history in this server.')
        @app_commands.guild_only()
        @app_commands.describe(wallet='Optional: choose a wallet. You can also change it in the balance panel.')
        async def wallet(interaction: discord.Interaction, wallet: str | None = None):
            await self.run_wallet(interaction, wallet=wallet)

        @self.tree.command(name='wallet-add', description='Moderators: add money to a player’s wallet.')
        @app_commands.guild_only()
        @app_commands.describe(player='Player receiving the money.', amount='Positive whole amount to add.',
                               wallet='Choose the currency wallet; required if there are several.', reason='Why you are adding money.')
        async def wallet_add(interaction: discord.Interaction, player: discord.Member,
                             amount: app_commands.Range[int, 1, MAX_MONEY], wallet: str | None = None,
                             reason: str = 'Session reward'):
            await self.run_wallet_change(interaction, player, amount, wallet=wallet, reason=reason)

        @self.tree.command(name='wallet-remove', description='Moderators: remove money from a player’s wallet.')
        @app_commands.guild_only()
        @app_commands.describe(player='Player paying the money.', amount='Positive whole amount to remove.',
                               wallet='Choose the currency wallet; required if there are several.', reason='Why you are removing money.')
        async def wallet_remove(interaction: discord.Interaction, player: discord.Member,
                                amount: app_commands.Range[int, 1, MAX_MONEY], wallet: str | None = None,
                                reason: str = 'Session expense'):
            await self.run_wallet_change(interaction, player, amount, wallet=wallet, reason=reason, remove=True)

        @self.tree.command(name='wallet-transfer', description='Moderators: transfer money between two players for a session deal.')
        @app_commands.guild_only()
        @app_commands.describe(sender='Player paying.', receiver='Player receiving payment.', amount='Positive whole amount to transfer.',
                               wallet='Choose the shared currency wallet; required if there are several.', reason='What the deal was for.')
        async def wallet_transfer(interaction: discord.Interaction, sender: discord.Member, receiver: discord.Member,
                                  amount: app_commands.Range[int, 1, MAX_MONEY], wallet: str | None = None,
                                  reason: str = 'Session deal'):
            await self.run_wallet_transfer(interaction, sender, receiver, amount, wallet=wallet, reason=reason)

        @self.tree.command(name='wallet-manage', description='Creator: name wallets and currency labels for this server.')
        @app_commands.guild_only()
        async def wallet_manage(interaction: discord.Interaction):
            await self.run_wallet(interaction, manage=True)

        async def wallet_suggestions(interaction: discord.Interaction, current: str):
            if not interaction.guild_id:
                return []
            return [app_commands.Choice(name=w['name'], value=f"wallet:{w['id']}")
                    for w in self.wallets.list(interaction.guild_id) if current.casefold() in w['name'].casefold()][:25]

        for command in (wallet, wallet_add, wallet_remove, wallet_transfer):
            command.autocomplete('wallet')(wallet_suggestions)

        @self.tree.command(name='builder', description='Server staff: build character rolls, named probability outcomes, FAQs, and shops.')
        @app_commands.guild_only()
        async def builder(interaction: discord.Interaction):
            await self.run_builder(interaction)

        @self.tree.command(name='custom', description='Explore or run this server’s custom character creators, FAQs, and shops.')
        @app_commands.guild_only()
        @app_commands.describe(command='Choose a published command, or leave blank to browse the collection.')
        async def custom(interaction: discord.Interaction, command: str | None = None):
            await self.run_custom(interaction, selection=command)

        @custom.autocomplete('command')
        async def suggestions(interaction: discord.Interaction, current: str):
            if not interaction.guild_id: return []
            return [app_commands.Choice(name=short('/' + c['name'] + ' · ' + c['published']['title']), value=f"cmd:{c['id']}")
                    for c in self.customs.list(interaction.guild_id, published=True)
                    if current.casefold() in (c['name'] + ' ' + c['published']['title']).casefold()][:25]

    async def setup_hook(self):
        synced = await self.tree.sync()
        log.info('Registered %s slash commands globally (v%s); Avatar, wallets, and server builder ready.', len(synced), __version__)

    def custom_command(self, record):
        async def callback(interaction: discord.Interaction):
            await self.run_custom(interaction, identity=record['id'])
        return app_commands.Command(name=record['name'], description=('Open ' + record['published']['title'])[:100], callback=callback)

    async def sync_custom_guild(self, guild, *, force=False):
        lock = self.custom_sync_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            if not force and guild.id in self.cleaned_guilds: return
            # Replace obsolete guild overrides with the persisted server command set.
            # Never copy global commands here or allow built-in names to be shadowed.
            self.tree.clear_commands(guild=guild)
            for record in self.customs.list(guild.id, published=True):
                self.tree.add_command(self.custom_command(record), guild=guild)
            try:
                await self.tree.sync(guild=guild)
                self.cleaned_guilds.add(guild.id)
            except discord.HTTPException:
                self.cleaned_guilds.discard(guild.id)
                raise

    async def clean_guild_commands(self, guild):
        if guild.id in self.cleaned_guilds:
            return
        try:
            await self.sync_custom_guild(guild)
        except discord.HTTPException as exc:
            log.warning('Server commands could not be synced: %s; will retry after reconnect.', type(exc).__name__)

    async def on_ready(self):
        for guild in self.guilds:
            await self.clean_guild_commands(guild)
        log.info('Dice bot connected as bot ID %s; %s server(s); version %s.', self.user.id, len(self.guilds), __version__)

    async def on_guild_join(self, guild):
        await self.clean_guild_commands(guild)
        log.info('Dice bot joined server ID %s; %s server(s).', guild.id, len(self.guilds))

    async def on_guild_remove(self, guild):
        self.cleaned_guilds.discard(guild.id)

    async def on_message(self, message):
        if (message.author.bot or message.author.id != self.engine.owner_id
                or not isinstance(message.channel, discord.DMChannel)
                or not COMMAND.match(message.content)):
            return
        if message.id == self.rigger_message_id:
            return
        try:
            if self.rigger_panel is not None:
                await self.rigger_panel.expire()
            panel = RiggerPanel(self)
            panel.message = await message.channel.send(embed=panel.embed(), view=panel, allowed_mentions=NO_MENTIONS)
            self.rigger_panel, self.rigger_message_id = panel, message.id
        except Exception as exc:
            log.error('Creator panel failed: %s.', type(exc).__name__)
            try:
                await message.channel.send('Could not open the panel. Try DMing !control again.', allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass

    async def error(self, interaction, text):
        message = '⚠️ ' + safe(text)
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True, allowed_mentions=NO_MENTIONS)
        else:
            if interaction.extras.pop('roll_deferred', False):
                # Complete a public defer without making its private error public.
                await interaction.edit_original_response(content='⚠️ **Could not finish this roll.** See the private reply.', allowed_mentions=NO_MENTIONS)
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def command_error(self, interaction, error):
        underlying = getattr(error, 'original', error)
        if isinstance(underlying, RollError):
            await self.error(interaction, str(underlying))
        else:
            log.error('Bot command failed: %s', type(underlying).__name__)
            await self.error(interaction, 'Could not finish this command. Please try again.')

    async def run_roll(self, interaction, *, dice=None, bonus=0, faces=None, keep='all'):
        try:
            if not interaction.guild_id:
                raise RollError('Use /roll in a server.')
            plan = prepare(dice, faces=faces, bonus=bonus, keep=keep)
        except RollError as exc:
            await self.error(interaction, str(exc))
            return
        # Acknowledge before a bounded search or the database's lock wait.
        await interaction.response.defer(thinking=True)
        interaction.extras['roll_deferred'] = True
        outcome = self.engine.roll_plan(interaction.user.id, interaction.guild_id, plan, request_id=interaction.id)
        parts = roll_messages(outcome['dice'], interaction.user.display_name)
        await self.deliver_roll(interaction, parts, outcome)

    async def run_avatar(self, interaction):
        if not interaction.guild_id:
            await self.error(interaction, 'Use /avatar in a server.')
            return
        await interaction.response.defer(thinking=True)
        interaction.extras['roll_deferred'] = True
        outcome = self.engine.avatar(interaction.user.id, interaction.guild_id, request_id=interaction.id)
        parts = avatar_messages(outcome['character'], interaction.user.display_name)
        await self.deliver_roll(interaction, parts, outcome)

    async def run_builder(self, interaction):
        if not allowed(interaction, 'faq'):
            await self.error(interaction, 'Use /builder as a server owner or moderator. Everyone can browse published commands with /custom.')
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        panel = BuilderPanel(self, interaction)
        panel.message = await interaction.edit_original_response(embed=panel.embed(), view=panel, allowed_mentions=NO_MENTIONS)

    async def run_custom(self, interaction, *, selection=None, identity=None):
        guild = getattr(interaction, 'guild', None)
        if guild is None or interaction.guild_id != guild.id:
            await self.error(interaction, 'Use custom commands in their server.')
            return
        try:
            if selection is not None:
                if not isinstance(selection, str) or not 1 <= len(selection) <= 100:
                    raise RollError('Choose a command from this server’s suggestions.')
                matches = self.customs.list(guild.id, published=True)
                record = next((c for c in matches if selection == f"cmd:{c['id']}" or
                               c['name'] == selection.strip().removeprefix('/').lower()), None)
                if record is None: raise RollError('That command is not published here. Use /custom to browse.')
                identity = record['id']
            if identity is None:
                await interaction.response.defer(thinking=True, ephemeral=True)
                panel = Catalogue(self, interaction)
                panel.message = await interaction.edit_original_response(embed=panel.embed(), view=panel, allowed_mentions=NO_MENTIONS)
                return
            record = self.customs.get(guild.id, identity, published=True)
            if record['kind'] == 'roll':
                await interaction.response.defer(thinking=True)
                interaction.extras['roll_deferred'] = True
                outcome = self.engine.custom_roll(interaction.user.id, guild.id, identity, request_id=interaction.id)
                embeds = character_embeds(outcome['character'], interaction.user.display_name)
                await interaction.edit_original_response(embed=embeds[0], allowed_mentions=NO_MENTIONS)
                interaction.extras.pop('roll_deferred', None)
                for embed in embeds[1:]: await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS)
                await self.finish_roll(outcome)
            else:
                await interaction.response.defer(thinking=True, ephemeral=True)
                panel = ContentPanel(self, interaction, record)
                panel.message = await interaction.edit_original_response(embed=panel.embed(), view=panel, allowed_mentions=NO_MENTIONS)
        except RollError as exc:
            await self.error(interaction, str(exc))

    async def run_wallet(self, interaction, *, wallet=None, manage=False):
        if not interaction.guild_id or interaction.guild is None or interaction.guild.id != interaction.guild_id:
            await self.error(interaction, 'Use /wallet in a server.')
            return
        if manage and interaction.user.id != self.engine.owner_id:
            await self.error(interaction, 'Only the bot creator can name wallets. Use /wallet to check your balance.')
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            panel = WalletPanel(self, interaction, wallet=wallet, manage=manage)
            panel.message = await interaction.edit_original_response(embed=panel.embed(), view=panel, allowed_mentions=NO_MENTIONS)
        except RollError as exc:
            await interaction.edit_original_response(content='⚠️ ' + safe(exc), allowed_mentions=NO_MENTIONS)

    async def wallet_moderator(self, interaction):
        if not interaction.guild_id or interaction.guild is None or interaction.guild.id != interaction.guild_id:
            await self.error(interaction, 'Use wallet commands in a server.')
            return False
        if not moderator(interaction):
            await self.error(interaction, 'Only server moderators can change money or record transfers. Use /wallet to check your balance.')
            return False
        return True

    async def run_wallet_change(self, interaction, player, value, *, wallet=None, reason='Session reward', remove=False):
        if not await self.wallet_moderator(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            selected = self.wallets.resolve(interaction.guild_id, wallet)
            target = await member(interaction, player.id)
            result = self.wallets.change(interaction.user.id, interaction.guild_id, selected['id'], target.id,
                value, reason, remove=remove, is_moderator=moderator(interaction), request_id=interaction.id)
            embed = discord.Embed(title='✅ Money ' + ('removed' if remove else 'added'), colour=TEAL,
                description=f"**{result['delta']:+,} {safe(result['wallet']['currency'])}** · {shown(target.display_name)}")
            embed.add_field(name='New balance', value=f"{result['balance']:,} {safe(result['wallet']['currency'])}", inline=True)
            embed.add_field(name='Wallet', value=safe(result['wallet']['name']), inline=True)
            embed.add_field(name='Reason', value=safe(reason), inline=False)
            embed.set_footer(text='Already saved · no duplicate change' if result['replayed'] else 'Saved in the player’s history')
            await self.wallet_confirmation(interaction, embed)
        except RollError as exc:
            await interaction.edit_original_response(content='⚠️ ' + safe(exc), allowed_mentions=NO_MENTIONS)

    async def run_wallet_transfer(self, interaction, sender, receiver, value, *, wallet=None, reason='Session deal'):
        if not await self.wallet_moderator(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            selected = self.wallets.resolve(interaction.guild_id, wallet)
            if sender.id == receiver.id:
                raise RollError('Choose two different players for a transfer.')
            source = await member(interaction, sender.id)
            destination = await member(interaction, receiver.id)
            result = self.wallets.transfer(interaction.user.id, interaction.guild_id, selected['id'], source.id,
                destination.id, value, reason, is_moderator=moderator(interaction), request_id=interaction.id)
            currency = safe(result['wallet']['currency'])
            embed = discord.Embed(title='💸 Transfer complete', colour=TEAL,
                description=f"**{result['amount']:,} {currency}** · {safe(result['wallet']['name'])}")
            embed.add_field(name='From · ' + shown(source.display_name), value=f"Balance: **{result['sender_balance']:,}**", inline=True)
            embed.add_field(name='To · ' + shown(destination.display_name), value=f"Balance: **{result['receiver_balance']:,}**", inline=True)
            embed.add_field(name='Deal', value=safe(reason), inline=False)
            embed.set_footer(text=f"Deal #{result['transfer_id']} · {'Already saved' if result['replayed'] else 'Recorded in both players’ histories'}")
            await self.wallet_confirmation(interaction, embed)
        except RollError as exc:
            await interaction.edit_original_response(content='⚠️ ' + safe(exc), allowed_mentions=NO_MENTIONS)

    async def wallet_confirmation(self, interaction, embed):
        try:
            await interaction.edit_original_response(embed=embed, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as exc:
            # A display failure must never invite a second award or transfer.
            log.warning('Saved currency change; confirmation failed: %s.', type(exc).__name__)
            try:
                await interaction.followup.send('The money change was saved, but its confirmation could not be displayed. '
                    'Check /wallet before submitting it again.', ephemeral=True, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass

    async def deliver_roll(self, interaction, parts, outcome):
        await interaction.edit_original_response(content=parts[0], allowed_mentions=NO_MENTIONS)
        interaction.extras.pop('roll_deferred', None)
        for part in parts[1:]:
            await interaction.followup.send(part, allowed_mentions=NO_MENTIONS)
        await self.finish_roll(outcome)

    async def finish_roll(self, outcome):
        if outcome['notice'] and not outcome['replayed']:
            await self.notify_creator(outcome['notice'])
        if not outcome['replayed'] and self.rigger_panel is not None:
            try:
                await self.rigger_panel.refresh_message()
            except discord.HTTPException as exc:
                log.warning('Private panel refresh failed: %s.', type(exc).__name__)

    async def notify_creator(self, notice):
        try:
            owner = self.get_user(self.engine.owner_id) or await self.fetch_user(self.engine.owner_id)
            for part in private_messages(notice):
                await owner.send(part, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as exc:
            log.warning('Creator notice could not be delivered: %s; the explanation is saved for creator status.', type(exc).__name__)

    async def close(self):
        if self.rigger_panel is not None:
            self.rigger_panel.stop()
        await super().close()
        if self.engine.store is not None:
            self.engine.store.close()
            self.engine.store = None
