"""Avatar character rolls. Each decision uses the final, possibly controlled roll."""

from .dice import prepare, values_of

PART_DICE = {'iq': '1d20', 'stats': '6d20', 'nation': '1d100', 'tribe': '1d3',
             'family': '1d100', 'control': '1d20', 'bending': '1d100',
             'element': '1d3', 'bending_strength': '1d20', 'combat': '1d100'}
PART_LIMITS = {'iq': (1, 20), 'stats': (6, 120), 'nation': (1, 100), 'tribe': (1, 3),
               'family': (1, 100), 'control': (1, 20), 'bending': (1, 100),
               'element': (1, 3), 'bending_strength': (1, 20), 'combat': (1, 100)}
# Slots address the existing ordered 6d20 pool; they do not add six more rolls.
STAT_PARTS = tuple(f'stat{index}' for index in range(1, 7))
PART_DICE.update({part: '1d20' for part in STAT_PARTS})
PART_LIMITS.update({part: (1, 20) for part in STAT_PARTS})
NATIONS = ('Fire Nation', 'Water Tribes', 'Earth Kingdom', 'Nomad')
TRIBES = ('Southern tribe', 'Northern tribe', 'Swamp tribe')
ELEMENTS = ('Fire', 'Water', 'Earth')
COMBAT = ('Sword', 'Bow', 'Fan', 'Fists', 'Pressure point')


def create_character(draw):
    """Draw receives (part, Plan). Conditional dice are never pre-rolled."""
    rolls = {}

    def take(part):
        rolls[part] = draw(part, prepare(PART_DICE[part]))
        return rolls[part]['total']

    iq = take('iq')
    take('stats')
    nation_roll = take('nation')
    nation = NATIONS[(nation_roll - 1) // 25]
    character = {'iq': iq, 'stats': values_of(rolls['stats']), 'nation': nation,
                 'tribe': None, 'family': None, 'fire_control': None,
                 'bender': False, 'bending': None, 'bending_strength': None,
                 'combat': None, 'rolls': rolls}
    if nation == 'Water Tribes':
        character['tribe'] = TRIBES[take('tribe') - 1]
    elif nation == 'Fire Nation':
        family = take('family')
        character['family'] = ('Royal family' if family >= 95 else
                               'High ranking family' if family >= 80 else
                               'Military family' if family >= 50 else 'Civilian family')
    elif nation == 'Earth Kingdom':
        character['fire_control'] = take('control') <= 15
    character['bender'] = take('bending') > 60
    if character['bender']:
        element = ELEMENTS[take('element') - 1] if nation == 'Nomad' else ELEMENTS[NATIONS.index(nation)]
        character['bending'] = element + ' bending'
        character['bending_strength'] = take('bending_strength')
    else:
        character['combat'] = COMBAT[(take('combat') - 1) // 20]
    return character
