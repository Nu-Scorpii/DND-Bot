"""Small, plain Discord messages. Public output receives only the dice result."""

import discord

LIMIT = 2000


def safe(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))


def length(text):
    return len(text.encode('utf-16-le')) // 2


def short(value, budget=100):
    """Bound component text in Discord's UTF-16 units without escaping labels."""
    value = str(value)
    while length(value) > budget:
        value = value[:-1]
    return value


def chunks(text, budget=LIMIT):
    while length(text) > budget:
        used, cut = 0, 0
        for char in text:
            width = 2 if ord(char) > 0xffff else 1
            if used + width > budget:
                break
            used += width
            cut += 1
        newline = text.rfind('\n', 0, cut + 1)
        if newline > 0:
            cut = newline
        # Avoid leaving a Markdown escape at the end of a message.
        while cut > 0 and text[cut - 1] == '\\':
            cut -= 1
        if cut == 0:
            cut = 1
        yield text[:cut]
        text = text[cut:].lstrip('\n')
    if text:
        yield text


def roll_messages(result, display_name='Player'):
    name = ' '.join(str(display_name).split())[:80] or 'Player'
    heading = f"🎲 **{safe(name)} rolled {safe(result['expression'])}**"
    continued = '🎲 **Roll continued**'
    pools = [group for group in result['groups'] if 'rolls' in group]
    lines = []
    for group in pools:
        label = 'Rolls' if len(pools) == 1 and group['sign'] == 1 else (
            ('− ' if group['sign'] < 0 else '') + f"{len(group['rolls'])}{group['die']}")
        for offset in range(0, len(group['rolls']), 10):
            values = group['rolls'][offset:offset + 10]
            shown = ' · '.join(f'`{value}`' if type(value) is int else safe(value) for value in values)
            lines.append(f"{label if offset == 0 else 'More rolls'}: {shown}")
        if group['kept'] is not None:
            lines.append(f"Kept ({'highest' if result['keep'] == 'high' else 'lowest'}): `{group['kept']}`")
    modifiers = [group['modifier'] for group in result['groups'] if 'modifier' in group]
    if result['bonus']:
        modifiers.append(result['bonus'])
    if len(modifiers) == 1:
        value = modifiers[0]
        label = 'Bonus' if value > 0 else 'Penalty' if value < 0 else 'Modifier'
        lines.append(f'{label}: `{value:+d}`')
    elif modifiers:
        shown = ' '.join(f'{value:+d}' for value in modifiers)
        lines.append(f'Modifiers: `{shown}` · net `{sum(modifiers):+d}`')
    if result['total'] is not None:
        lines.extend(('', f"**Total: {result['total']}**"))
    budget = LIMIT - max(length(heading), length(continued)) - 2
    return [(heading if index == 0 else continued) + '\n\n' + part
            for index, part in enumerate(chunks('\n'.join(lines), budget))]


def private_messages(text):
    return ['🔒 **Creator controls**\n\n' + part for part in chunks(safe(text), 1900)]


def avatar_messages(character, display_name='Player'):
    name = ' '.join(str(display_name).split())[:80] or 'Player'
    rolls = character['rolls']

    def shown(label, value, part):
        return f"**{label}:** {value} · `{rolls[part]['total']}` ({rolls[part]['expression']})"

    lines = [f"🎲 **{safe(name)} · Avatar character**", '',
             f"**IQ:** `{character['iq']}` (1d20)",
             '**Stats:** ' + ' · '.join(f'`{value}`' for value in character['stats']) + ' (6d20)', '',
             shown('Nation', character['nation'], 'nation')]
    if character['tribe']:
        lines.append(shown('Tribe', character['tribe'], 'tribe'))
    if character['family']:
        lines.append(shown('Family', character['family'], 'family'))
    if character['fire_control'] is not None:
        lines.append(shown('Village under Fire Nation control', 'Yes' if character['fire_control'] else 'No', 'control'))
    lines.extend(('', shown('Bending', character['bending'] or 'Non-bender', 'bending')))
    if 'element' in rolls:
        lines.append(shown('Nomad element', character['bending'], 'element'))
    # Older saved receipts predate this stat; replay them without drawing again.
    if 'bending_strength' in rolls:
        lines.append(f"**Bending Strength:** `{rolls['bending_strength']['total']}` (1d20)")
    if character['combat']:
        lines.append(shown('Combat style', character['combat'], 'combat'))
    return list(chunks('\n'.join(lines)))
