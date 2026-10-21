"""Verify the accepted source snapshot without external dependencies."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'docs/delivery/accepted-source-manifest.json').read_text(encoding='utf-8'))
failures = []
for item in manifest['files']:
    path = root / item['path']
    if not path.is_file():
        failures.append(item['path'] + ': missing')
        continue
    data = path.read_bytes()
    if item['encoding'] == 'utf-8-lf':
        data = data.replace(b'\r\n', b'\n')
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        failures.append(item['path'] + ': changed')
if failures:
    print('\n'.join(failures))
    print('Differences from the accepted snapshot; preserve intentional development changes.')
    sys.exit(1)
print(f"Verified {len(manifest['files'])} accepted source files.")
