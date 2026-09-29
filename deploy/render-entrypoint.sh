#!/bin/sh
set -eu

chown mosh:mosh /var/data
chmod 0700 /var/data

exec setpriv --reuid=10001 --regid=10001 --init-groups python -m mosh_core.production
