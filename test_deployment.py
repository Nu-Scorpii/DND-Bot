import asyncio
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from ua_bot.__main__ import database_path, validate
from build_release import build

ROOT = Path(__file__).resolve().parents[1]


def test_offline_tree_check():
    asyncio.run(validate())


def test_release_contains_only_minimal_runtime_and_runs_offline(tmp_path):
    artifact = build(ROOT, tmp_path / 'bot.pyz')
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        assert set(names) == {'__main__.py'} | {f'ua_bot/{name}.py' for name in
                         ['__init__', '__main__', 'errors', 'dice', 'solver', 'controls', 'storage', 'engine', 'display', 'client', 'avatar', 'rigger_ui', 'luck', 'wallets', 'wallet_ui', 'custom', 'custom_ui', 'builder_ui']}
        assert archive.testzip() is None
    result = subprocess.run([sys.executable, str(artifact), '--check'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Validated 9 global slash commands: /roll, /avatar, /wallet' in result.stdout


def test_docker_uses_single_runtime_file_and_validates_it():
    docker = (ROOT / 'Dockerfile').read_text()
    assert 'COPY bot.pyz ./bot.pyz' in docker
    assert 'RUN python bot.pyz --check' in docker
    assert 'COPY ua_bot' not in docker
    assert 'python' in (ROOT / 'railway.toml').read_text()


def test_railway_defaults_to_volume_database(tmp_path):
    volume = tmp_path / 'volume'
    assert database_path({'RAILWAY_SERVICE_ID': 'service', 'RAILWAY_VOLUME_MOUNT_PATH': str(volume)}) == str(volume / 'ua.sqlite3')
    assert database_path({}) == 'data/ua.sqlite3'


@pytest.mark.parametrize('environment', [
    {'RAILWAY_SERVICE_ID': 'service'},
    {'RAILWAY_SERVICE_ID': 'service', 'RAILWAY_VOLUME_MOUNT_PATH': 'relative'},
    {'RAILWAY_SERVICE_ID': 'service', 'RAILWAY_VOLUME_MOUNT_PATH': '/data', 'BOT_DB_PATH': 'local.sqlite3'},
    {'RAILWAY_SERVICE_ID': 'service', 'RAILWAY_VOLUME_MOUNT_PATH': '/data', 'BOT_DB_PATH': '/data'},
    {'RAILWAY_SERVICE_ID': 'service', 'RAILWAY_VOLUME_MOUNT_PATH': '/data', 'BOT_DB_PATH': '/data/../ephemeral.sqlite3'}])
def test_railway_rejects_missing_volume_and_ephemeral_paths(environment):
    with pytest.raises(SystemExit, match='persistent volume'):
        database_path(environment)
