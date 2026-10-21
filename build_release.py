"""Build a single-file deployment from the readable, canonical source files."""

from pathlib import Path
import zipfile

ENTRYPOINT = 'from ua_bot.__main__ import main\nmain()\n'


def build(root=None, destination=None):
    root = Path(root or Path(__file__).resolve().parent)
    destination = Path(destination or root/'bot.pyz')
    sources = sorted((root/'ua_bot').glob('*.py'))
    for source in sources:
        compile(source.read_text(encoding='utf-8'),str(source),'exec')
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        archive.writestr('__main__.py',ENTRYPOINT)
        for source in sources:
            archive.write(source,source.relative_to(root).as_posix())
    return destination


if __name__ == '__main__':
    artifact = build()
    print(f'Built {artifact.name}; {artifact.stat().st_size:,} bytes. Upload it with Dockerfile, requirements.txt, and railway.toml.')
