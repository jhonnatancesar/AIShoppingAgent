#!/bin/sh
set -eu

display_number="${AISHOPPING_XVFB_DISPLAY:-99}"
export DISPLAY=":${display_number}"

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp -ac &

exec "$@"
