"""Small, exact two-number calculator for Discord slash commands."""

from decimal import Decimal, localcontext
import re
from typing import NamedTuple


NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
EXPRESSION = re.compile(
    rf'\s*(?P<left>{NUMBER})\s*(?P<left_percent>%)?\s*'
    rf'(?P<operation>[+*/×÷-])\s*'
    rf'(?P<right>{NUMBER})\s*(?P<right_percent>%)?\s*\Z'
)
USAGE = 'Enter two numbers with +, -, * or /, for example 100 + 20% or 12.5 / 2.'


class CalculatorError(ValueError):
    """The expression is invalid or cannot be calculated."""


def formatted(value):
    """Show decimal results without binary float rounding or trailing zeroes."""
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


class Calculation(NamedTuple):
    expression: str
    result: Decimal
    show_percent: bool

    @property
    def answer(self):
        value = formatted(self.result)
        if self.show_percent:
            value += f' ({formatted(self.result * 100)}%)'
        return value


def calculate(expression: str) -> Calculation:
    """Calculate exactly two operands. A right-hand percent in + or - is relative to the left number."""
    if not isinstance(expression, str) or len(expression) > 100:
        raise CalculatorError(USAGE)
    match = EXPRESSION.fullmatch(expression)
    if not match or any(sum(c.isdigit() for c in match[name]) > 18 for name in ('left', 'right')):
        raise CalculatorError(USAGE)

    left_percent = bool(match['left_percent'])
    right_percent = bool(match['right_percent'])
    left = Decimal(match['left']) / (100 if left_percent else 1)
    right = Decimal(match['right']) / (100 if right_percent else 1)
    operation = match['operation']
    if operation in ('/', '÷') and right == 0:
        raise CalculatorError('Cannot divide by zero.')

    with localcontext() as context:
        context.prec = 40
        if operation in ('+', '-'):
            # The usual calculator interpretation of 100 + 20% is 120.
            # Two percentages add as portions: 20% + 5% = 25%.
            if right_percent and not left_percent:
                right *= left
            result = left + right if operation == '+' else left - right
        elif operation in ('*', '×'):
            result = left * right
        else:
            result = left / right
    return Calculation(expression.strip(), result, left_percent and right_percent and operation in ('+', '-'))
