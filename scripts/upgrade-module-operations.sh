#!/bin/bash
# Upgrade only the existing MyChuannong service. No credentials or database exports in this bundle.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
if [[ $EUID -ne 0 || $# -ne 4 ]]; then
  echo 'Usage: sudo bash upgrade-module-operations.sh ARCHIVE SHA256 RELEASE EXPECTED_CURRENT' >&2
  exit 2
fi
archive=$(realpath "$1")
digest=$2
release=$3
expected=$4
[[ $digest =~ ^[0-9a-f]{64}$ && $release =~ ^[0-9a-f]{40}$ && $expected =~ ^[0-9a-f]{40}$ ]]
app=/opt/mychuannong
previous=$(readlink -f "$app/current")
[[ $previous == "$app/releases/$expected" ]] || { echo 'Current release changed; stop and review.' >&2; exit 1; }
[[ ! -e "$app/releases/$release" ]] || { echo 'Release directory already exists; stop and review.' >&2; exit 1; }
[[ $(sha256sum "$archive" | cut -d' ' -f1) == "$digest" ]] || { echo 'Archive checksum mismatch.' >&2; exit 1; }
staging=$(mktemp -d "$app/releases/.module-upgrade-XXXXXX")
chmod 755 "$staging"
stopped=0
activated=0
switch_link="$app/current.module-upgrade.$$"
switch_to() {
  rm -f -- "$switch_link"
  ln -s "$1" "$switch_link"
  mv -Tf "$switch_link" "$app/current"
}
cleanup() {
  result=$?
  trap - EXIT
  if [[ $result -ne 0 && $stopped -eq 1 ]]; then
    if [[ $activated -eq 1 ]]; then switch_to "$previous"; fi
    systemctl restart mychuannong-backend || true
    echo 'Upgrade failed; previous application restored. Additive schema and backup preserved.' >&2
  fi
  # Remove only the temporary extraction directory created by this execution.
  if [[ -d $staging ]]; then rm -rf -- "$staging"; fi
  exit "$result"
}
trap cleanup EXIT
# Accept only regular source files and directories; reject archive traversal and links.
python3 - "$archive" <<'PY'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    for item in archive.getmembers():
        p = PurePosixPath(item.name)
        if p.is_absolute() or '..' in p.parts or not (item.isfile() or item.isdir()):
            raise SystemExit('Unsupported archive member')
PY
tar -xzf "$archive" -C "$staging" --no-same-owner
cmp "$previous/requirements.lock.txt" "$staging/requirements.lock.txt" >/dev/null || {
  echo 'Dependencies changed; this upgrade requires a separate review.' >&2; exit 1;
}
systemctl stop mychuannong-backend
stopped=1
runuser -u mychuannong -- "$app/venv/bin/python" - "$staging" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.database_tools import backup_database
backup_database(Path('/var/lib/mychuannong/my-chuannong.db'),
                Path('/var/backups/mychuannong'), prefix='before-module-operations', prune=False)
print('Pre-upgrade database backup verified.')
PY
mv "$staging" "$app/releases/$release"
switch_to "releases/$release"
activated=1
systemctl start mychuannong-backend
ready=0
for attempt in {1..20}; do
  if curl -fsS --max-time 2 -H 'Host: chuannong-api.mychangliapp.cn' \
    http://127.0.0.1:8792/api/v1/ready >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ $ready -eq 1 ]] || { echo 'Readiness check failed.' >&2; exit 1; }
stopped=0
echo "MyChuannong upgraded to $release; readiness passed. Previous release: $expected"
