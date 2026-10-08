#!/bin/sh
set -eu
# Render's persistent disk replaces the image directory. Prepare only our data directory.
install -d -m 700 -o 10001 -g 10001 /var/lib/pararadar
exec gosu pararadar uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --no-access-log
