"""Parse first, roll second. No saved dice, game rules, or private settings."""

import json
import random
import re
from dataclasses import dataclass
from .errors import RollError, require

RNG = random.SystemRandom()
MAX_DICE = 100
MAX_SIDES = 1_000_000_000
NAME = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
TERM = re.compile(r'\s*([+-]?)\s*(?:(\d*)d(\d+|\[[a-z][a-z0-9_]{0,31}\])|(\d+))')
INLINE_DIE = re.compile(r'd(?=\[)', re.IGNORECASE)
NAMED_DIE = re.compile(r'\[[a-z][a-z0-9_]{0,31}\]', re.IGNORECASE)
KEEP = re.compile(r'(\d*d(?:\d+|\[[a-z][a-z0-9_]{0,31}\]))(kh1|kl1)', re.IGNORECASE)


@dataclass(frozen=True)
class Die:
    label: str
    sides: int | None = None
    faces: tuple | None = None

    @property
    def numeric(self):
        return self.sides is not None or all(type(x) is int for x in self.faces)

    def contains(self, value):
        if self.sides is not None:
            return type(value) is int and 1 <= value <= self.sides
        return any(type(value) is type(face) and value == face for face in self.faces)

    def roll(self, rng):
        return rng.randint(1, self.sides) if self.sides is not None else rng.choice(self.faces)


@dataclass(frozen=True)
class Plan:
    expression: str
    terms: tuple
    bonus: int
    keep: str

    @property
    def dice(self):
        return tuple((sign, die) for sign, term in self.terms if isinstance(term, tuple) for die in term)

    @property
    def numeric(self):
        return all(die.numeric for _, die in self.dice)

    @property
    def modifier(self):
        return self.bonus + sum(sign * term for sign, term in self.terms if type(term) is int)

    def result(self, values):
        require(len(values) == len(self.dice), 'The number of dice results does not match the roll.')
        require(all(die.contains(value) for (_, die), value in zip(self.dice, values)),
                'A result is not a face on its die.')
        groups, offset, total = [], 0, self.bonus
        numeric = self.numeric
        for sign, term in self.terms:
            if type(term) is int:
                groups.append({'modifier': sign * term})
                total += sign * term
                continue
            rolled = list(values[offset:offset + len(term)])
            offset += len(term)
            kept = max(rolled) if self.keep == 'high' else min(rolled) if self.keep == 'low' else None
            if numeric:
                total += sign * (kept if kept is not None else sum(rolled))
            groups.append({'die': term[0].label, 'sign': sign, 'rolls': rolled, 'kept': kept})
        return {'expression': self.expression, 'groups': groups, 'bonus': self.bonus,
                'keep': self.keep, 'total': total if numeric else None}

    def roll(self, rng=RNG):
        return self.result([die.roll(rng) for _, die in self.dice])


def custom_faces(source):
    if source is None or source == '':
        return {}
    if isinstance(source, str):
        require(len(source) <= 20_000, 'Keep the faces list under 20,000 characters.')
        try:
            source = json.loads(source)
        except (ValueError, RecursionError) as exc:
            raise RollError('Use JSON faces, such as [0,1,1,5] or ["sun","rain"].') from exc
    if isinstance(source, list):
        source = {'custom': source}
    require(isinstance(source, dict) and 1 <= len(source) <= 20,
            'Faces must be a JSON list, or an object with named lists.')
    result = {}
    for name, faces in source.items():
        require(isinstance(name, str) and NAME.fullmatch(name), 'Die names use lowercase letters, numbers, and underscores; start with a letter.')
        require(isinstance(faces, list) and 1 <= len(faces) <= 1000, 'Each custom die needs 1–1,000 faces. Repeated faces add weight.')
        require(all(type(face) is int and abs(face) <= MAX_SIDES or
                    isinstance(face, str) and 1 <= len(face) <= 40 and not any(ord(c) < 32 for c in face)
                    for face in faces), 'Faces must be whole numbers within ±1 billion or text labels of 1–40 characters.')
        result[name] = tuple(faces)
    return result


def inline_options(expression, custom, keep):
    """Read optional JSON faces and standard kh1/kl1 from the same dice field."""
    pieces, position = [], 0
    while match := INLINE_DIE.search(expression, position):
        pieces.append(expression[position:match.end()])
        start = match.end()
        named = NAMED_DIE.match(expression, start)
        if named:
            pieces.append(named.group())
            position = named.end()
            continue
        try:
            faces, end = json.JSONDecoder().raw_decode(expression, start)
        except (ValueError, RecursionError) as exc:
            raise RollError('Use custom dice like 1d[0,1,1,5] or 1d["sun","rain"].') from exc
        name = f'inline{len(custom)}'
        while name in custom:
            name += 'x'
        require(len(custom) < 20, 'Use at most 20 custom dice lists.')
        custom.update(custom_faces({name: faces}))
        pieces.append(f'[{name}]')
        position = end
    pieces.append(expression[position:])
    parsed = ''.join(pieces).lower()
    selections = [match.group(2) for match in KEEP.finditer(parsed)]
    require(len(selections) <= 1, 'Use kh1 or kl1 once, on a single dice pool.')
    if selections:
        chosen = 'high' if selections[0] == 'kh1' else 'low'
        require(keep in ('all', chosen), 'Choose either highest or lowest, not both.')
        keep = chosen
        parsed = KEEP.sub(r'\1', parsed)
    return parsed, keep


def prepare(expression=None, *, faces=None, bonus=0, keep='all'):
    custom = custom_faces(faces)
    if expression is None:
        expression = f'1d[{next(iter(custom))}]' if custom else '1d20'
    require(isinstance(expression, str), 'Dice must be text, such as 2d20+3.')
    expression = expression.strip().replace('−', '-')
    require(0 < len(expression) <= 500, 'Use 1–500 characters for the dice expression.')
    require(keep in ('all', 'high', 'low'), 'Keep must be all, high, or low.')
    require(type(bonus) is int and abs(bonus) <= MAX_SIDES, 'Bonus must be a whole number within ±1 billion.')
    parsed, keep = inline_options(expression, custom, keep)
    terms, count, position = [], 0, 0
    while position < len(parsed):
        match = TERM.match(parsed, position)
        require(match is not None, 'Use dice like 1d100, 1d100+2, or 1d100-2. Custom faces: 1d[0,1,1,5].')
        sign, amount, source, modifier = match.groups()
        require(not terms or sign, 'Separate dice and modifiers with + or −.')
        sign = -1 if sign == '-' else 1
        if modifier is not None:
            require(int(modifier) <= MAX_SIDES, 'Modifiers are limited to ±1 billion.')
            term = int(modifier)
        else:
            amount = int(amount) if amount else 1
            count += amount
            require(amount >= 1 and count <= MAX_DICE, 'Roll 1–100 dice at a time.')
            if source.startswith('['):
                name = source[1:-1]
                require(name in custom, f'Give the faces directly, such as 1d[0,1,1,5], instead of d[{name}].')
                label = 'd[custom]' if name.startswith('inline') else f'd[{name}]'
                die = Die(label, faces=custom[name])
            else:
                sides = int(source)
                require(1 <= sides <= MAX_SIDES, 'Numbered dice can have 1–1 billion sides.')
                die = Die(f'd{sides}', sides=sides)
            term = (die,) * amount
        terms.append((sign, term))
        require(len(terms) <= 20, 'Use at most 20 dice/modifier terms.')
        position = match.end()
        if not parsed[position:].strip():
            break
    require(count > 0, 'Include at least one die.')
    plan = Plan(expression, tuple(terms), bonus, keep)
    pools = [(sign, term) for sign, term in terms if isinstance(term, tuple)]
    require(keep == 'all' or len(pools) == 1 and pools[0][0] == 1,
            'Keep-high/low uses one positive dice pool, such as 2d20+3.')
    if not plan.numeric:
        require(keep == 'all' and bonus == 0 and all(sign == 1 for sign, _ in terms)
                and all(isinstance(term, tuple) for _, term in terms),
                'Dice with text faces cannot use arithmetic, bonus points, or keep-high/low.')
    return plan


def values_of(result):
    return [value for group in result['groups'] if 'rolls' in group for value in group['rolls']]
