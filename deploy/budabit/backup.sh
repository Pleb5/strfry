#!/usr/bin/env bash

set -Eeuo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
volume_root=${STRFRY_BACKUP_VOLUME_ROOT:-/mnt/HC_Volume_105751807}
backup_root=${STRFRY_BACKUP_ROOT:-$volume_root/backups/strfry}
compose=(docker compose --project-directory "$deploy_dir" -f "$deploy_dir/compose.yaml")

volume_root=$(realpath -e "$volume_root")
backup_root=$(realpath -m "$backup_root")

if ! mountpoint -q "$volume_root"; then
    printf 'Backup volume is not mounted: %s\n' "$volume_root" >&2
    exit 1
fi

if [[ $volume_root != / && $backup_root != "$volume_root"/* ]]; then
    printf 'Backup target is outside the backup volume: %s\n' \
        "$backup_root" >&2
    exit 1
fi

mkdir -p "$backup_root/daily" "$backup_root/weekly" "$backup_root/monthly"

if [[ $(stat -c %d "$backup_root") != $(stat -c %d "$volume_root") ]]; then
    printf 'Backup target is not on the configured backup volume: %s\n' \
        "$backup_root" >&2
    exit 1
fi

exec 9>"$backup_root/.backup.lock"
if ! flock -n 9; then
    printf 'Another strfry backup is already running\n' >&2
    exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="events-$stamp.jsonl.zst"
temporary=$(mktemp "$backup_root/daily/.$name.tmp.XXXXXX")
final="$backup_root/daily/$name"

cleanup() {
    rm -f "$temporary"
}
trap cleanup EXIT

"${compose[@]}" exec -T relay \
    /usr/local/bin/strfry --config /etc/strfry.conf export \
    | zstd -T1 -q -f -o "$temporary"

mv "$temporary" "$final"
trap - EXIT

(
    cd "$backup_root/daily"
    sha256sum "$name" > "$name.sha256"
)
config_snapshot="$backup_root/daily/strfry-$stamp.conf"
"${compose[@]}" cp relay:/etc/strfry.conf "$config_snapshot"
touch "$config_snapshot"

promote_snapshot() {
    local target=$1
    local period=$2
    local marker="$target/.period-$period"

    if [[ -e $marker ]]; then
        return
    fi

    ln "$final" "$target/$name"
    ln "$final.sha256" "$target/$name.sha256"
    ln "$config_snapshot" "$target/strfry-$stamp.conf"
    touch "$marker"
}

promote_snapshot "$backup_root/weekly" "$(date -u +%G-W%V)"
promote_snapshot "$backup_root/monthly" "$(date -u +%Y-%m)"

find "$backup_root/daily" -type f -mtime +7 -delete
find "$backup_root/weekly" -type f -mtime +35 -delete
find "$backup_root/monthly" -type f -mtime +190 -delete

printf 'Created strfry backup: %s\n' "$final"
