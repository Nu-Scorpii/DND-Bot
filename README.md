# Simple Discord Dice Bot · 4.1.0

Players use `/roll`, `/avatar`, `/wallet`, and their server's custom commands. Server staff use `/builder` to create character rolls, FAQs, and shops. Moderators use separate currency commands to add, remove, or transfer money.

## New: your server's command workshop

Run **`/builder` → New command**. Choose **Character rolls**, **FAQ / guide**, or **Shop**. Give it a name such as `hero`, `rules`, or `market`; add entries, Preview, then Publish. Players can use its own slash command (`/hero`, `/rules`, `/market`) or browse **`/custom`**. These names are examples you create, not additional built-ins.

Character templates support separate named groups such as **Origin · 1d5**, **Stats · 6d20**, and **Power · 1d20+2**, all in one invocation. Add odds tables with named outcomes and exact relative weights, plus optional conditions based on earlier results. Pick a display title, introduction, and accent colour. The six stat dice stay visible as six values.

**Named probabilities** are available under **Add outcome**. Define a separate outcome type such as Clan and give each result an exact chance, for example `Uchiha | 5%`, `Hyuga | 10%`, `Uzumaki | 15%`, and `Other clan | 70%`. These are example campaign odds, not canonical Naruto probabilities. The chances must total 100%. Players see only the selected name—no numeric die, expression, or total. You can combine several probability types with normal dice groups in one command, or publish a command containing just one probability type. Conditions can use the selected name, for example `Clan == Uchiha`. **Relative weights** remains available for named results that also display a numeric draw.

Server owners, Administrator, or Manage Server can create/edit character templates. Moderators can also create/edit FAQs and shops. Published commands are available to all server players, subject to Discord's own application-command permissions. Shops charge the buyer's chosen server currency wallet after a confirmation and save purchases in **My items**. They have unlimited stock. FAQs use a question dropdown.

Drafts, published commands, shop inventories, and wallet data survive redeploys. The built-in Avatar command is preserved. Read **[BUILDER_GUIDE.md](BUILDER_GUIDE.md)** for a complete walkthrough, sample odds and conditions, permissions, and limits.

## Players: `/roll`

Type `/roll`, select its **dice** field, and enter your roll. There is just one field. Add `+2` for a bonus or `-2` for a penalty in the same expression. Leave it blank for **1d20**. Discord command names are lowercase.

| Enter in dice | What it does |
|---|---|
| `1d100` | Roll one 100-sided die |
| `1d100+2` | Roll, then add 2 |
| `1d100-2` | Roll, then subtract 2 |
| `6d20+3` | Roll six d20s, then add 3 once |
| `2d17+1d8-3` | Mix dice sizes, then subtract 3 |

Examples show the value to put in Discord's single dice field:

```text
/roll
/roll dice:1d100+2
/roll dice:1d100-2
/roll dice:2d17+1d8-3
```

The message shows every die, any **Bonus** or **Penalty**, and the final **Total**. No modifier line is added for a plain roll. Multiple fixed modifiers are shown individually with their net change. For example, if `1d100-2` rolls 50:

```text
🎲 Hero rolled 1d100-2

Rolls: 50
Penalty: -2

Total: 48
```

Dice values use code formatting and the heading and total are bold. Long rolls are split into readable messages. Input mistakes receive private errors.

Advanced rolls still use that same field:

| Enter in dice | What it does |
|---|---|
| `3d20kh1+2` | Keep the highest of three d20s, then add 2 |
| `3d20kl1-2` | Keep the lowest, then subtract 2 |
| `2d[0,1,1,5]+2` | Roll two weighted custom dice, then add 2 |
| `1d["sun","rain","rain","storm"]` | Roll a text result; repeated faces add weight |
| `1d[-5,8]+1d[1,2,5]` | Mix custom numeric dice |

Custom lists use JSON; text labels need double quotes. Face spelling and capitalization are preserved. Highest/lowest uses one positive dice pool. A die containing any text face has no numeric total and cannot use arithmetic or highest/lowest.

Limits: 100 dice, 1–1 billion sides, 20 expression terms, and a **500-character dice field**, including any custom lists. Lists have at most 1,000 faces each, within that expression limit; text labels have 1–40 characters. Fixed modifiers are whole numbers within ±1 billion. No programming expressions are evaluated.

## Players: `/avatar`

Run `/avatar` in a server. There are no options to fill in. The bot rolls the whole character in this order:

1. **IQ:** 1d20.
2. **Stats:** six separate results from 6d20, in order. Assign your own stat names; the bot does not invent them.
3. **Nation:** 1d100, followed by its applicable origin detail.
4. **Bending:** 1d100. **61–100** means bending; **1–60** means non-bender.
5. A Nomad bender rolls an element; a non-bender rolls a combat style.
6. **Bending Strength:** every bender rolls a separate **1d20**. Non-benders skip this roll entirely.

| Nation roll | Origin | Extra origin roll |
|---|---|---|
| 1–25 | Fire Nation | 1d100 for family |
| 26–50 | Water Tribes | 1d3: 1 Southern, 2 Northern, 3 Swamp |
| 51–75 | Earth Kingdom | 1d20: 1–15 village under Fire Nation control, 16–20 no |
| 76–100 | Nomad | None |

Fire Nation families use **1–49 Civilian, 50–79 Military, 80–94 High ranking, 95–100 Royal**. The supplied ranges overlapped at 50, 80, and 95; this resolves those boundaries in favor of the higher tier.

A Fire Nation bender gets Fire bending, a Water Tribes bender gets Water bending, and an Earth Kingdom bender gets Earth bending. **Only a Nomad whose final bending check is above 60** rolls **1d3: 1 Fire, 2 Water, 3 Earth**. A result of exactly 60 is a non-bender and rolls combat style instead. Nomad is an origin, not an Air Nomad. **There is no Air bending.** Every character makes one bending check; there is no second chance check for an element.

Non-benders roll **1d100: 1–20 Sword, 21–40 Bow, 41–60 Fan, 61–80 Fists, 81–100 Pressure point**. The shared endpoint 80 belongs to Fists. Inapplicable dice are never rolled: no tribe outside Water Tribes, no family outside Fire Nation, no village control outside Earth Kingdom, no combat style for benders, and no extra element roll except for Nomad benders.

The message shows the six stat values and each applicable category with its actual roll. Benders also get **Bending Strength: 1–20**, separate from IQ and the six stats; non-benders have no Bending Strength line. It is a character creation result, not a saved or tracked character sheet. Repeat `/avatar` only when your GM permits a new character roll.

For example, a bender's message includes `Bending Strength: 17 (1d20)`. The stat does not change the existing bending threshold or determine the element.

## Wallets: simple commands

**Everyone can use `/wallet`.** It opens your own private balance card with a wallet dropdown, History, and Refresh. There are no money-editing buttons in this player command, even for moderators or the creator. Existing wallets stay available. A server without a wallet automatically gets **Main / coins**, with every player starting at zero; no creator setup is required to check your balance.

| Command | Who can use it | What it does |
|---|---|---|
| `/wallet [wallet]` | Every player | Your balance, recent activity, and paged history |
| `/wallet-add player amount [wallet] [reason]` | Server moderators | Add money to a selected player |
| `/wallet-remove player amount [wallet] [reason]` | Server moderators | Remove money without allowing an overdraft |
| `/wallet-transfer sender receiver amount [wallet] [reason]` | Server moderators | Move money between two players for a session deal |
| `/wallet-manage` | Bot creator | Create wallets or edit names and currency labels |

Examples show the values to choose in Discord's slash options:

```text
/wallet
/wallet-add player:@Alex amount:100 reason:Quest reward
/wallet-remove player:@Alex amount:25 reason:Shop purchase
/wallet-transfer sender:@Alex receiver:@Sam amount:75 reason:Sword purchase
/wallet-transfer sender:@Alex receiver:@Sam amount:75 wallet:Avatar reason:Sword purchase
/wallet-manage
```

Select real members with Discord's player picker; select wallet names from the autocomplete suggestions, or type an exact wallet name. If there is one wallet, money commands can omit it. **If there are several wallets, money commands require you to choose one** to avoid changing the wrong campaign's money. `/wallet` can omit it and lets you switch with the dropdown.

**Only actual server moderators can add, remove, or transfer money.** Accepted permissions are Administrator, Manage Server, Manage Messages, Timeout Members (Moderate Members), Kick Members, or Ban Members. Permission checks run in the bot for every command, including after member checks. Being the bot creator or having a role named Mod does not bypass them. `/wallet` has no moderator permission restriction; server admins can still control application-command access through Discord's own server settings.

**Transfers pay the receiver from the sender's balance.** They do not create money. Both players must be different current human members of the same server, using the same named currency wallet. An unaffordable payment or a receiver exceeding the balance limit changes neither account. Debit, credit, both history entries, and the duplicate-request receipt save together. Two simultaneous payments cannot spend the same money twice.

Every change records the moderator, amount, reason, date, and resulting balance. Transfers also appear in both players' histories with the other player's ID and the same deal number. Moderator confirmations are private, and players see their updated balance with `/wallet` or Refresh. Reasons default to Session reward, Session expense, or Session deal if omitted.

The creator uses **`/wallet-manage`**, then New wallet or Edit wallet to name up to **25 wallets per server** and their currency labels. The automatic Main wallet can be renamed. Renaming keeps all balances and history; changing a label does not convert money. Wallets and balances remain separate for each server, wallet, and player.

Panels close after ten minutes of inactivity or a restart; use `/wallet` again. All saved wallets, balances, old history, and durable currency receipts survive redeploys. Existing version-3.5 data migrates without resetting anything. Wallet commands do not draw dice. Whole currency units and the existing **1 trillion** per-player balance limit remain.

## Railway

For this existing service, retain its attached volume and database path, token, and creator ID. Replace these **four files at the repository root**, then deploy the latest commit:

```text
Dockerfile
requirements.txt
railway.toml
bot.pyz
```

Upload the extracted files, rather than the download ZIP. Keep `bot.pyz` as a file. It already contains the entire minimal runtime, so a flattened GitHub upload cannot cause a missing `ua_bot` folder build error. Railway runs `python bot.pyz`; the Dockerfile validates it during the build. Repository Root Directory should be blank or `/`. Keep one replica and a continuously running service; no public domain or HTTP healthcheck is needed.

For a new service, attach a persistent volume at **`/data`**. Set these variables through your own dashboard:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | Your existing Discord bot token |
| `BOT_OWNER_ID` | Your own Discord user ID |
| `BOT_DB_PATH` | Optional: `/data/ua.sqlite3`; omit it to use the actual attached volume automatically |

Railway supplies `RAILWAY_VOLUME_MOUNT_PATH`; do not set it manually. The database must be inside that mount. A local default is `data/ua.sqlite3`. No server ID is needed; a previous `DISCORD_GUILD_ID` is ignored. The bot registers nine global commands and restores each server's published custom commands when it connects, replacing obsolete guild overrides. Discord clients may need a refresh before their cached menus update.

The bot preserves old campaign tables on the volume without loading them. The last 1,000 dice receipts, wallet balances, history, and currency receipts are stored separately. A repeated recent dice interaction returns its original result; duplicate currency submissions are remembered independently of the dice cache.

Install the Discord application **to your server** with `bot` and `applications.commands` scopes; adding it only to your account does not put the bot in the server. Permit View Channel, Send Messages, and Embed Links for the cards, and let players use application commands. Leave the Interactions Endpoint URL empty. No privileged Gateway intents or Administrator permission are needed. A startup log showing **0 server(s)** means the connected bot was not in any servers when it connected: check its bot ID against the bot in your server and use the correct server install.

## Local editing and checks

Use Python 3.12, extract the full source package, and run:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ua_bot --check
python build_release.py
python bot.pyz --check
```

For local play, copy `.env.example` to `.env`, fill in your token and creator ID, and run `python bot.pyz`. Keep credentials out of source uploads. Rebuild `bot.pyz` after editing source; Railway runs the bundle, not the loose source files. The full ZIP has the proper `ua_bot/` and `tests/` folders for editing. Four-file uploads are for deployment only.

## Troubleshooting

- Bot online but `/roll`, `/avatar`, or `/wallet` missing: check its server installation, `applications.commands` scope, and channel permissions; refresh Discord's command menu.
- Checking a new server: `/wallet` creates an empty Main wallet automatically. The creator can rename it or create more with `/wallet-manage`.
- Adding or moving money: use `/wallet-add`, `/wallet-remove`, or `/wallet-transfer` with actual server moderation permissions. The player balance panel only shows your money and history. Choose a wallet if several exist.
- Old commands still visible: refresh Discord. Runtime logs report server-command sync failures; a reconnect retries them. Published custom commands are preserved.
- Volume error: attach the volume to this service and place the database under its real mount, then redeploy. Retain the original database file when updating.
Official API reference: https://discordpy.readthedocs.io/en/stable/interactions/api.html
