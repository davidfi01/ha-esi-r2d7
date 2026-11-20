"""Validate JSON, sync translations, and package the Home Assistant custom integration."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent
component = root / 'custom_components' / 'esi_r2d7'
for name in ('manifest.json', 'strings.json'):
    json.loads((component / name).read_text(encoding='utf-8'))

# Home Assistant shows text for custom integrations only from translations/<lang>.json.
# strings.json is the source; en.json is regenerated here so the two cannot drift.
translations = component / 'translations'
translations.mkdir(exist_ok=True)
(translations / 'en.json').write_bytes((component / 'strings.json').read_bytes())

manifest = json.loads((component / 'manifest.json').read_text(encoding='utf-8'))
if any(req.lower().startswith('serialx') for req in manifest.get('requirements', [])):
    raise SystemExit('FAIL: do not pin serialx; Home Assistant core supplies it via the usb dependency')

zip_path = root / 'esi_r2d7_custom_integration.zip'
with ZipFile(zip_path, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(component.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            archive.write(path, path.relative_to(root).as_posix())
    archive.write(root / 'README.md', 'README.md')
print('PASS: validated JSON, synced translations, created ZIP for version', manifest['version'], ':', zip_path)
with ZipFile(zip_path) as archive:
    print('Archive contents:', ', '.join(archive.namelist()))
