#!/usr/bin/bash

set -eux

# if all containers started at the same time, pg is definitely not ready to serve
# so let's try this for a few times
ATTEMPTS=7
n=0
while [[ $n -lt $ATTEMPTS ]]; do
  alembic-3 upgrade head && break
  n=$((n+1))
  sleep 2
done

# If the number of attempts was exhausted: the migration failed.
# Exit with an error.
if [[ $n -eq $ATTEMPTS ]]; then
    echo "Migration failed after $ATTEMPTS attempts. Exiting."
    exit 1
fi

# Simple Flask development server configuration
export PACKIT_SERVICE_CONFIG="${HOME}/.config/packit-service.yaml"
HTTP_PORT="${HTTP_PORT:-8080}"

echo "Starting Flask development server on 0.0.0.0:${HTTP_PORT}"

# Use Flask's built-in development server
cd /usr/share/packit
exec python3 -m flask --app packit.wsgi run --host=0.0.0.0 --port="${HTTP_PORT}" --debug 