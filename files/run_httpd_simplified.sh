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

# Simple development server configuration
export PACKIT_SERVICE_CONFIG="${HOME}/.config/packit-service.yaml"
HTTP_PORT="${HTTP_PORT:-8080}"

echo "Starting development server on localhost:${HTTP_PORT}"

# Simple development server using mod_wsgi-express without SSL
exec mod_wsgi-express-3 start-server \
    --access-log \
    --log-to-terminal \
    --port "${HTTP_PORT}" \
    --server-name "localhost" \
    --processes 1 \
    --threads 4 \
    --locale "C.UTF-8" \
    --url-alias / /usr/share/packit/packit.wsgi \
    /usr/share/packit/packit.wsgi 