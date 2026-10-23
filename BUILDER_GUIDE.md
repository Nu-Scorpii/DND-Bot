# Your server's command workshop · 4.2.0

**Start with `/builder`.** The workshop opens a private card with dropdowns, buttons, and forms. Pick **New command**, choose a type, and name the slash command. All changes save as drafts; **Publish** makes them available to players.

## Make a character creator

Create a **Character rolls** command named `hero`, with a title such as *Create your hero*. Use **Add dice** three times:

| Roll name | Dice expression | Result |
|---|---|---|
| Origin | `1d5` | One result from 1–5 |
| Stats | `6d20` | Six separate d20 values and their total |
| Power | `1d20+2` | A separate d20, a visible +2 bonus, and its total |

Use **Preview** to check the draft, then **Publish**. A player runs `/hero` once and receives all three groups in one character result. Long results continue in extra messages. Players can also use `/custom command:hero` or run `/custom` with no option and use its catalogue.

Every group has its own label and dice expression. Use ordinary dice sizes, mixed pools, modifiers, keep-high/low, or custom faces through the same syntax as `/roll`. Examples: `2d17+1d8-3`, `3d20kh1+2`, `1d[0,1,1,5]`, and `1d["sun","rain"]`. Text faces use double quotes. Dice groups are separate; do not put comma-separated expressions in one normal dice field. Add another group instead.

If each stat needs its own name, add six named `1d20` entries. A single `6d20` group is useful when you want six values together.

## Create a named probability type

Use **Add outcome → Named probabilities**. Name the type, such as `Clan`, `Family`, `Rarity`, or `Bending`, and enter one result and its chance per line:

```text
Uchiha | 5%
Hyuga | 10%
Uzumaki | 15%
Other clan | 70%
```

Each invocation chooses one named outcome with those exact chances. A player might see **Clan: Uchiha**, with no numeric roll, die size, or total. These are example campaign odds, not canonical Naruto probabilities. You do not need to create a die or calculate number ranges. The editor shows the names and configured percentages; player messages and replay receipts contain only the selected name.

Chances must add up to **exactly 100%**. The editor rejects an incomplete or excessive total rather than changing your odds. Positive decimal chances up to four places work, such as `0.0001%` and `99.9999%`. The `%` suffix is optional in this mode; `25` means 25%. Remove an outcome to disable it; zero/negative chances and duplicate names are rejected. Editing and saving preserves the exact chances without rounding.

Add multiple outcome types to the same character command and mix them with `1d5`, `6d20`, separate `1d20` groups, modifiers, and conditions. A command may also contain only a named probability type. Each type belongs to the command where you created it. Use **Edit** to change its name, outcomes, chances, or condition, then Publish to update the live command.

For example, create `Bending` with `Bender | 40%` and `Non-bender | 60%`, then add `Bending Strength` as `1d20` with condition `Bending == Bender`. Only a chosen Bender receives the extra d20.

## Add classes and subclasses

Use **Add outcome → Class + subclasses** in a character command. Set the class roll name (for example `Class`) and enter the classes and their chances:

```text
Fighter | 60%
Mage | 40%
```

Set the player label for the second result (for example `Subclass`), then enter one subclass per line with its parent class and chance:

```text
Fighter | Champion | 70%
Fighter | Guardian | 30%
Mage | Evoker | 25%
Mage | Illusionist | 75%
```

The class chances must total **100%**, and the subclasses of **each** class must separately total **100%**. Give every class at least one subclass; one subclass can be `100%`. The form saves one class entry plus one conditional subclass entry for each class. For example, **Class: Mage** rolls only the Mage subclass table, and the player sees **Subclass: Evoker** or **Subclass: Illusionist**. Both results show names only. This uses one entry per class plus one for the class roll, within the command's 20 entry limit.

In the editor, each subclass entry has a distinct name such as `Subclass · Mage`, a **Player label** of `Subclass`, and a condition pointing to its class. Edit its probabilities or player label as needed. If you change a class outcome's name, also update its matching subclass entry's condition so it still runs. Preview the draft, then Publish to update the live command.

## Relative weights as an alternative

**Add outcome → Relative weights** keeps the earlier numeric-draw style. Enter `Rare | 1` and `Common | 3` for 25% and 75%. These weights do not need to total 100. Positive decimals with up to four places work. This mode displays both the chosen name and a numeric draw. Use Named probabilities when you want only a named result.

Relative weights are reduced to equivalent integer tickets. Four equal weights of 25 use `1d4`, with the same 25% chance for each outcome. Older relative-weight tables retain their output and behavior. Both modes support conditions; no custom programming is required.

## Add a roll only when it is needed

Choose a roll and press **Condition**, or fill in the optional condition field while adding it. Examples:

| Condition | Meaning |
|---|---|
| `Bending >= 61` | Roll only if the earlier Bending total is at least 61 |
| `Nation == Water` | Roll only if the earlier Nation table selected Water |
| `Bending == Bender` | Roll only if a named probability type selected Bender |
| `Family != Civilian family` | Skip civilians |
| `Power < 10` | Roll only below a total of 10 |

The source must appear **earlier** in the template. Numeric comparisons use the final total including modifiers. Named comparisons use the exact outcome spelling and capitalization. Operators are `==`, `!=`, `>`, `<`, `>=`, and `<=`; names/text support only equality and inequality. A roll can have one condition. If its source was skipped, it is also skipped, allowing chains of conditions. No programming code or compound `and/or` expressions are evaluated.

Renaming an earlier roll preserves its existing conditions. Removing a source or moving it below a dependent is rejected until you remove/fix the condition.

## Publish, style, and maintain commands

- **Name & style:** change the display title, introduction, and six-digit accent colour, such as `8B5CF6`. Unpublish first to change the slash name.
- **Edit / Remove entry:** modify the selected roll, question, or shop item.
- **Move up / Move down:** change entry order; roll conditions must still point backward.
- **Preview:** private sample output from the draft. It uses sample dice, publishes nothing, and buys nothing.
- **Publish:** copy the draft into the live command and update the server's slash menu. Later draft edits do not change it until published again.
- **Unpublish:** remove player access while retaining the draft.
- **Archive / Restore:** hide an old command and restore its draft later. Restoring does not publish it. Archived commands still occupy their slot and reserve their name; reuse them when needed.
- **Help:** opens the in-bot guide. Select a command again to reload it if another editor changed the draft.

Each server has its own definitions. The same slash name may exist in different servers with different content. Reserved names such as `/avatar`, `/roll`, `/wallet`, `/builder`, `/custom` cannot be overwritten. Slash names use lowercase letters, numbers, underscores, or hyphens, starting with a letter.

If Discord's slash-menu update fails, the saved publication remains available immediately through `/custom`. The panel tells you; Publish/Unpublish again to retry, or reconnect the bot. Discord clients may need a command-menu refresh. Published commands are restored at startup.

## Make an FAQ or guide

Choose **New command → FAQ / guide**, name it `rules` (or another available name), and use **Add question** to write questions and answers. Set its title, introduction, and colour. Preview and Publish.

Players run `/rules` and pick a question in the private dropdown. Answers are split safely when long. The panel includes Refresh. You can make multiple separate guides for different campaign topics.

## Make a shop

Choose **New command → Shop**, name it `market`, then use **Add item** for an item name, whole-number price, and description. Use **Wallet** to choose the existing server wallet whose currency pays for purchases. A server with no wallet automatically gets Main / coins. The bot creator can create or rename currencies through `/wallet-manage`.

After publishing, players run `/market`, browse items, and see their own balance and each item's price. **Buy item** asks for quantity, then opens a **Review purchase** card. Only **Confirm purchase** charges their wallet. **Cancel** spends nothing. Purchases appear under **My items** and in wallet history.

Shops have **unlimited stock**. Item ownership is tracked as quantities; using, equipping, selling back, player-to-player item transfers, limited stock, and automatic game effects are not implemented. A shop purchase removes currency from the buyer; it does not pay a player-owned vendor. For a deal between two players, moderators still use `/wallet-transfer`.

Editing an item keeps its identity and accumulated ownership. Removing and re-adding it creates a new item; old purchases remain in My items while the shop is accessible. Archiving or unpublishing a shop preserves its records but disables its player panel until republished.

Only moderators can award money, remove money manually, or transfer it between players. A purchase can charge only the buyer's own balance after confirmation. There is no debt or partial purchase. A price or publication change invalidates an old quote; reopen the shop to review it. Duplicate confirmation of the same quote cannot charge twice, including after restart. Opening a new review card creates a new purchase opportunity.

## Permissions and saved data

| Action | Who can do it |
|---|---|
| Build/edit character commands | Server owner, Administrator, or Manage Server |
| Build/edit FAQs and shops | The above, plus Manage Messages, Timeout Members, Kick Members, or Ban Members |
| Run published commands, read FAQs, browse/buy from shops | All server players, subject to Discord's command permissions |
| Award/remove money or record player transfers | Actual server moderators |
| Name/create currency wallets | Bot creator |

Having a role named Mod is not sufficient. The editor checks actual current permissions, the opener, the server, and the saved draft version. Two editors cannot silently overwrite each other's changes: a stale form asks you to select the command again. Private panels close after ten minutes of inactivity or restart; reopen the command for a new panel.

Drafts, publications, inventory quantities, balances, and transaction history are saved in the existing Railway volume. No new token, server ID variable, volume, or service is needed for the existing installation. Character outputs are messages and recent replay receipts, not an editable saved character sheet; your GM still controls rerolls.

## Limits

| Setting | Limit |
|---|---|
| Command slots per server | 20, including archived commands |
| Entries per command | 20 |
| Base dice per character invocation | 200 total; 100 per expression |
| Named outcomes per probability type or weights table | 25 |
| Named probability precision | Up to four decimal places; total exactly 100% |
| Relative-weight precision | Up to four decimal places; normalized ticket total at most 1 billion |
| Dice expression | 500 characters, existing `/roll` limits apply |
| FAQ answer | 1,500 characters |
| Shop quantity per purchase | 1–100 |
| Inventory quantity per player/item | 1,000,000 |
| Prices, purchase totals, wallet balance | At most 1 trillion whole currency units |

The workshop creates these three supported command types; it does not execute arbitrary scripts or add arbitrary Discord administration actions. The existing `/avatar` creation flow, Bending Strength, normal `/roll`, wallets, and transfers remain available.
