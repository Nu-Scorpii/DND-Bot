"""python -m ua_bot starts the bot; --check validates it without Discord login."""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
from dotenv import load_dotenv
from .client import DiceBot
from .engine import DiceEngine
from .storage import Store


def database_path(environment):
    """Use Railway's real runtime volume by default; reject ephemeral overrides."""
    configured = environment.get('BOT_DB_PATH','').strip()
    railway = any(environment.get(name) for name in ['RAILWAY_ENVIRONMENT_ID','RAILWAY_PROJECT_ID','RAILWAY_SERVICE_ID'])
    if railway:
        mount = environment.get('RAILWAY_VOLUME_MOUNT_PATH','').strip()
        if not mount or not Path(mount).is_absolute():
            raise SystemExit('Railway needs an attached persistent volume, recommended mount /data. Attach it, then redeploy.')
        volume = Path(mount).resolve()
        path = Path(configured or volume/'ua.sqlite3').resolve()
        if path == volume or not path.is_relative_to(volume):
            raise SystemExit('Railway needs its database inside the persistent volume. Use BOT_DB_PATH=/data/ua.sqlite3 with a volume mounted at /data.')
        return str(path)
    return configured or 'data/ua.sqlite3'


async def validate():
    store = Store(':memory:')
    bot = DiceBot(DiceEngine(store, 1))
    commands = bot.tree.get_commands()
    # Serialize the actual Discord tree: catches unsupported options and groups.
    payloads = [c.to_dict(bot.tree) for c in commands]
    assert len(payloads) <= 100
    def check(node, depth=0):
        assert 1 <= len(node['name']) <= 32
        assert len(node.get('description', '')) <= 100
        assert len(node.get('options', [])) <= 25
        assert len(node.get('choices', [])) <= 25
        assert depth <= 3
        size = len(node['name']) + len(node.get('description', ''))
        for choice in node.get('choices', []):
            assert len(choice['name']) <= 100
            size += len(choice['name']) + (len(choice['value']) if isinstance(choice['value'], str) else 0)
        return size + sum(check(child, depth + 1) for child in node.get('options', []))
    for payload in payloads:
        assert check(payload) <= 8000, f"/{payload['name']} exceeds Discord's command size limit."
    assert [payload['name'] for payload in payloads] == ['roll', 'avatar', 'wallet', 'wallet-add', 'wallet-remove', 'wallet-transfer', 'wallet-manage', 'builder', 'custom']
    assert [option['name'] for option in payloads[0]['options']] == ['dice']
    assert payloads[1].get('options', []) == []
    assert payloads[2]['default_member_permissions'] is None
    assert [o['name'] for o in payloads[2].get('options', [])] == ['wallet']
    assert [o['name'] for o in payloads[5]['options']] == ['sender', 'receiver', 'amount', 'wallet', 'reason']
    assert [o['name'] for o in payloads[8].get('options', [])] == ['command']
    print('Validated 9 global slash commands: /roll, /avatar, /wallet, /wallet-add, /wallet-remove, /wallet-transfer, /wallet-manage, /builder, /custom. Server commands are restored separately. No network login performed.')
    await bot.close()


async def run():
    # Explicit cwd works for both ordinary modules and a zip-imported bot.pyz.
    load_dotenv(dotenv_path=Path.cwd()/'.env')
    token = os.getenv('DISCORD_TOKEN', '').strip()
    owner = os.getenv('BOT_OWNER_ID', '').strip()
    if not token or token == 'put_your_bot_token_here' or not owner.isdecimal() or int(owner) <= 0:
        raise SystemExit('Set DISCORD_TOKEN and BOT_OWNER_ID in your environment. BOT_OWNER_ID is your Discord user ID, not the application ID.')
    path = database_path(os.environ)
    bot = DiceBot(DiceEngine(Store(path), int(owner)))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
        except (NotImplementedError, RuntimeError):
            pass
    try:
        async with bot:
            await bot.start(token)
    except __import__('discord').LoginFailure:
        raise SystemExit('Discord rejected the bot token. Replace DISCORD_TOKEN with the current token from the Bot page.') from None


def main():
    parser = argparse.ArgumentParser(description='Simple Discord dice bot')
    parser.add_argument('--check', action='store_true', help='Validate commands offline without credentials')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(validate() if args.check else run())


if __name__ == '__main__':
    main()
