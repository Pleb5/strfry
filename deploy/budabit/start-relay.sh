#!/bin/sh
set -eu
# An old image without this entrypoint cannot silently serve a private preset.
if [ "${1:-relay}" = relay ]; then
    python3 /usr/local/lib/strfry/check-read-control.py --config-only
fi
exec /usr/local/bin/strfry "$@"
