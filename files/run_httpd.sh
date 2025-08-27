#!/usr/bin/bash

set -eux

export PACKIT_SERVICE_CONFIG="${HOME}/.config/packit-service.yaml"
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
# See "mod_wsgi-express-3 start-server --help" for details on
# these options, and the configuration documentation of mod_wsgi:
# https://modwsgi.readthedocs.io/en/master/configuration.html
exec gunicorn \
  -w 2 \
  -b 0.0.0.0:8443 \
  --certfile /secrets/fullchain.pem \
  --keyfile /secrets/privkey.pem \
  packit_service.service.app:application
