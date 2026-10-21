"""Validated private controls and the internal legacy-command adapter."""

import re
import shlex
from .avatar import PART_DICE, PART_LIMITS
from .errors import RollError, require

HELP = ('DM !control to use the private panel. Choose a player and roll, then Apply. '
        'Use Luck % for 1–100% strength, Stat 1–6 for individual dice, and Active settings '
        'to check or remove saved controls. Live DMs do not execute command arguments.')

MAX_VALUE = 200_000_000_000
MAX_RIGS = 100
MAX_USES = 1_000_000
MAX_EXTRA_DICE = 100
EXTRA_MODES = ('extra_high', 'extra_low')
PASSIVE_MODES = ('good', 'bad', *EXTRA_MODES)
TARGET = re.compile(r'(?:<@!?(\d+)>|(\d+))\Z')
NUMBER = re.compile(r'[+-]?\d+\Z')
COMMAND = re.compile(r'!(?:control|rigger)(?:\s|$)', re.IGNORECASE)
CUSTOM_SCOPE = re.compile(r'custom:[1-9]\d{0,18}\Z')
CUSTOM_FIELD = re.compile(r'field_[a-f0-9]{12}\Z')


def is_custom_scope(scope):
    return isinstance(scope, str) and CUSTOM_SCOPE.fullmatch(scope) is not None


def empty_setting():
    return {'mode': 'off', 'value': None, 'target': None, 'part': None, 'scope': 'roll', 'last': None}


def player_id(value):
    """Accept a copied user ID or mention without depending on a guild cache."""
    match = TARGET.fullmatch(str(value).strip())
    require(match and 0 < int(match[1] or match[2]) < 2**64,
            'Paste a Discord user ID or mention. Right-click the player and choose Copy User ID.')
    return str(int(match[1] or match[2]))


def rig_uses(mode, uses='default'):
    """None means permanent; old defaults are one use or lasting luck."""
    if uses == 'default':
        return None if mode in PASSIVE_MODES else 1
    if uses is None or isinstance(uses, str) and uses.strip().lower() == 'permanent':
        return None
    require(isinstance(uses, (str, int)) and not isinstance(uses, bool)
            and len(str(uses)) <= 20 and re.fullmatch(r'\d+', str(uses).strip()),
            'Enter a whole number of uses, such as 3, or permanent.')
    uses = int(str(uses).strip())
    require(1 <= uses <= MAX_USES, 'Choose 1–1,000,000 uses, or permanent.')
    return uses


def panel_setting(mode, value=None, target=None, *, scope='roll', part=None, uses='default'):
    """Validate a panel draft before changing the active setting."""
    require(mode in ('off', 'force', 'shift', 'good', 'bad', 'face', *EXTRA_MODES), 'Choose a control mode.')
    if mode == 'off':
        return empty_setting()
    custom = is_custom_scope(scope)
    require(scope in ('roll', 'avatar') or custom, 'Choose /roll, an Avatar roll, or a custom character command.')
    require(part is None or isinstance(part, str) and (bool(CUSTOM_FIELD.fullmatch(part)) if custom else part in PART_DICE),
            'Choose a roll from the panel.')
    require(scope != 'roll' or part is None, 'Avatar parts only work on /avatar.')
    require(scope != 'avatar' or part is not None or mode in PASSIVE_MODES,
            'Whole-character controls support luck or extra dice. Choose a specific part to force or shift it.')
    require(part != 'stats' or mode in PASSIVE_MODES,
            'Choose Stat 1–6 to force or shift an individual die. Six stats supports luck or extra dice.')
    require(not custom or part is not None or mode in PASSIVE_MODES,
            'Whole custom characters support luck or extra dice. Choose a specific roll for an exact change.')
    target = None if target is None else player_id(target)
    if mode in ('force', 'shift'):
        require(isinstance(value, (str, int)) and not isinstance(value, bool)
                and len(str(value)) <= 30 and NUMBER.fullmatch(str(value).strip()),
                'Enter a whole number using Enter value.')
        value = int(str(value).strip())
        require(abs(value) <= MAX_VALUE, 'Use a value within ±200 billion.')
        if part is not None and mode == 'force' and not custom:
            low, high = PART_LIMITS[part]
            require(low <= value <= high, f'For {part}, choose a value from {low} to {high}.')
    elif mode in EXTRA_MODES:
        require(isinstance(value, (str, int)) and not isinstance(value, bool)
                and len(str(value)) <= 8 and re.fullmatch(r'\d+', str(value).strip()),
                'Enter a whole number of extra dice. Use 4 for five candidates per result.')
        value = int(str(value).strip())
        require(1 <= value <= MAX_EXTRA_DICE,
                f'Choose 1–{MAX_EXTRA_DICE} extra dice per result. Use Remove selected to turn it off.')
    elif mode == 'face':
        require(scope == 'roll' or custom and part is not None, 'Text faces need /roll or a specific custom text roll.')
        require(isinstance(value, str) and 1 <= len(value) <= 40 and not any(ord(c) < 32 for c in value),
                'Enter the exact text face, using 1–40 characters.')
    else:
        value = 100 if value is None else value
        text = str(value).strip().removesuffix('%').strip()
        require(isinstance(value, (str, int)) and not isinstance(value, bool)
                and len(str(value)) <= 8 and NUMBER.fullmatch(text),
                'Enter a whole luck strength from 1 to 100.')
        value = int(text)
        require(1 <= value <= 100, 'Luck strength must be 1–100%. Use Remove selected to turn it off.')
    return {'mode': mode, 'value': value, 'target': target, 'part': part, 'scope': scope,
            'remaining': rig_uses(mode, uses), 'last': None}


def parse_command(content):
    require(isinstance(content, str) and len(content) <= 500, 'Keep the command under 500 characters.')
    try:
        words = shlex.split(content)
    except ValueError as exc:
        raise RollError('Close your quotation marks. Example: !Control face "heavy rain".') from exc
    args = words[1:]
    if not args:
        return 'help', None
    first = args.pop(0).lower()
    part = None
    if first == 'avatar':
        require(args, 'Choose an Avatar part. Example: !Control avatar nation 30.')
        first = args.pop(0).lower()
        require(first in PART_DICE, 'Choose: iq, stats, nation, tribe, family, control, bending, element, bending_strength, or combat.')
    if first in PART_DICE:
        part = first
        require(args, f'Give a number, shift, good, or bad. Example: !Control {part} 10.')
        first = args.pop(0).lower()
    if first in ('help', 'status', 'off'):
        require(part is None, f'Use !Control {first} by itself.')
        require(not args, f'Use !Control {first} by itself.')
        return first, None
    if first in ('force', 'shift', 'face', 'luck'):
        require(args, 'Give a value. Examples: !Control 15, !Control +3, !Control good.')
        value = args.pop(0)
        mode = first
        if first == 'luck':
            mode, value = value.lower(), None
            require(mode in ('good', 'bad'), 'Use !Control good or !Control bad.')
    elif first in ('good', 'bad'):
        mode, value = first, None
    elif NUMBER.fullmatch(first):
        mode, value = ('shift' if first.startswith(('+', '-')) else 'force'), first
    else:
        raise RollError('Use !Control 15, !Control +3, !Control good, !Control bad, or !Control off. See !Control help.')
    if mode in ('force', 'shift'):
        require(isinstance(value, str) and NUMBER.fullmatch(value), 'Use a whole number, such as 15 or -2.')
        value = int(value)
        require(abs(value) <= MAX_VALUE, 'That number is too large; use a value within ±200 billion.')
    if mode == 'face':
        require(part is None, 'Avatar dice are numeric. Use a number, shift, good, or bad.')
        require(1 <= len(value) <= 40 and not any(ord(c) < 32 for c in value), 'Use a text face of 1–40 characters, with quotes for spaces.')
    if part is not None and mode == 'force':
        low, high = PART_LIMITS[part]
        require(low <= value <= high, f'For {part}, force a total from {low} to {high}.')
    require(len(args) <= 1, 'Give only one optional target: @User or their user ID.')
    target = None
    if args:
        match = TARGET.fullmatch(args[0])
        require(match and 0 < int(match[1] or match[2]) < 2**64, 'The target must be @User or a positive Discord user ID.')
        target = str(int(match[1] or match[2]))
    return 'set', {'mode': mode, 'value': value, 'target': target, 'part': part,
                   'scope': 'avatar' if part else 'all', 'last': None}


def describe(setting):
    mode, value = setting['mode'], setting['value']
    if mode == 'off':
        return 'Control is off. Rolls are normal.'
    label = {'force': f'Next final total: {value}', 'shift': f'Next total shift: {value:+d}' if mode == 'shift' else '',
             'face': f'Next text face: {value}', 'good': f'Good luck: {value or 100}% until removed',
             'bad': f'Bad luck: {value or 100}% until removed',
             'extra_high': f'Extra dice: {value} per result; keep highest',
             'extra_low': f'Extra dice: {value} per result; keep lowest'}[mode]
    scope = (f"\nRoll: {setting['scope']} · {setting.get('part') or 'whole character'}." if is_custom_scope(setting.get('scope')) else
             f"\nRoll: /avatar {setting['part']} only." if setting.get('part') else
             '\nRoll: all /avatar parts.' if setting.get('scope') == 'avatar' else
             '\nRoll: /roll only.' if setting.get('scope') == 'roll' else '\nRoll: /roll and /avatar (legacy setting).')
    return label + scope + '\nTarget: ' + (f"user {setting['target']} in every server." if setting['target'] else 'anyone in any server.')


def empty_controls():
    return {'rules': [], 'next_id': 1, 'last': None}


def category(rule):
    if rule['mode'] in EXTRA_MODES:
        return 'extra'
    return 'luck' if rule['mode'] in ('good', 'bad') else rule['mode']


def rule_key(rule):
    """Applying a draft replaces only the same player's slot and kind."""
    return rule['target'], rule['scope'], rule['part'], category(rule)
