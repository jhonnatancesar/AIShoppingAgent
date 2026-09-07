#!/bin/sh
set -eu
if [ ! -s /run/secrets/searxng ]; then
    echo 'SearXNG runtime secret file missing or empty' >&2
    exit 1
fi
SEARXNG_SECRET="$(cat /run/secrets/searxng)"
export SEARXNG_SECRET
exec /usr/local/searxng/entrypoint.sh "$@"
