import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ua_bot.calculator import CalculatorError, calculate
from ua_bot.client import DiceBot


@pytest.mark.parametrize(('expression', 'expected'), [
    ('2 + 3', '5'),
    ('2 - 3', '-1'),
    ('2.5 * 4', '10'),
    ('1 / 4', '0.25'),
    ('100 + 20%', '120'),
    ('100 - 20%', '80'),
    ('100 * 20%', '20'),
    ('100 / 20%', '500'),
    ('20% + 5%', '0.25'),
    ('20% - 5%', '0.15'),
    ('20% * 50%', '0.1'),
    ('20% / 5%', '4'),
    ('20% + 5', '5.2'),
    ('-100 + 20%', '-120'),
    ('10 / -2', '-5'),
    ('0.1 + 0.2', '0.3'),
    ('12 × .5', '6'),
    ('12 ÷ 3', '4'),
])
def test_calculator_arithmetic(expression, expected):
    assert calculate(expression).result == Decimal(expected)


def test_percentage_result_is_labeled():
    assert calculate('20% + 5%').answer == '0.25 (25%)'
    assert calculate('100 + 20%').answer == '120'


@pytest.mark.parametrize('expression', ['1 / 0', '1 / 0%', '1 + 2 + 3', '2 ** 3',
                                         'import os', '@everyone + 2', '9' * 19 + ' + 1'])
def test_invalid_calculations_are_rejected(expression):
    with pytest.raises(CalculatorError):
        calculate(expression)


def test_calculator_command_and_private_errors(make_engine):
    async def check():
        engine = make_engine()
        bot = DiceBot(engine)
        command = bot.tree.get_command('calculate')
        options = command.to_dict(bot.tree)['options']
        assert [(option['name'], option['required']) for option in options] == [('expression', True)]

        def event():
            return SimpleNamespace(response=SimpleNamespace(is_done=MagicMock(return_value=False),
                                                               send_message=AsyncMock(), defer=AsyncMock()),
                                   followup=SimpleNamespace(send=AsyncMock()), extras={})

        good = event()
        await command.callback(good, '100 + 20%')
        assert '**Result: 120**' in good.response.send_message.await_args.args[0]
        assert good.response.send_message.await_args.kwargs['allowed_mentions'] is not None
        good.response.defer.assert_not_awaited()

        bad = event()
        await command.callback(bad, '1 / 0')
        assert 'divide by zero' in bad.response.send_message.await_args.args[0]
        assert bad.response.send_message.await_args.kwargs['ephemeral'] is True
        assert engine.rng.calls == 0
        await bot.close()
    asyncio.run(check())
