#!/usr/bin/bash

set -eux

export PACKIT_SERVICE_CONFIG="${HOME}/.config/packit-service.yaml"

# See "mod_wsgi-express-3 start-server --help" for details on
# these options, and the configuration documentation of mod_wsgi:
# https://modwsgi.readthedocs.io/en/master/configuration.html
exec gunicorn \
  -w 2 \
  -b 0.0.0.0:8443 \
  --certfile /secrets/fullchain.pem \
  --keyfile /secrets/privkey.pem \
  packit_service.service.app:application
