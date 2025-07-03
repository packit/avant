# true|false
PULL_BASE_IMAGE ?= true
SERVICE_IMAGE ?= quay.io/packit/packit-service:dev
PACKIT_PATH ?= ../packit
CONTAINER_ENGINE ?= $(shell command -v podman 2> /dev/null || echo docker)
ANSIBLE_PYTHON ?= $(shell command -v /usr/bin/python3 2> /dev/null || echo /usr/bin/python2)
AP ?= ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)
PATH_TO_SECRETS ?= $(CURDIR)/secrets/
COV_REPORT ?= --cov=packit_service --cov-report=term-missing
COLOR ?= yes
SOURCE_BRANCH ?= $(shell git branch --show-current)
CONTAINER_RUN_INTERACTIVE ?= -it
COMPOSE ?= docker-compose
MY_ID ?= `id -u`

service: files/install-deps.yaml files/recipe.yaml
	$(CONTAINER_ENGINE) build --rm \
		--pull=$(PULL_BASE_IMAGE) \
		-t $(SERVICE_IMAGE) \
		-f files/docker/Dockerfile \
		--build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) \
		.

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=$(COLOR) --verbose --showlocals $(COV_REPORT) ./tests/unit ./tests/integration/

compose-for-db-up:
	$(COMPOSE) up --build --force-recreate -d service

migrate-db: compose-for-db-up
	sleep 10 # service pod have to be up and running "alembic upgrade head"
	podman run --rm -ti --user $(MY_ID) --uidmap=$(MY_ID):0:1 --uidmap=0:1:999 \
	-e DEPLOYMENT=dev \
	-e REDIS_SERVICE_HOST=redis \
	-e POSTGRESQL_USER=packit \
	-e POSTGRESQL_PASSWORD=secret-password \
	-e POSTGRESQL_HOST=postgres \
	-e POSTGRESQL_DATABASE=packit \
	-v $(CURDIR)/alembic:/src/alembic:rw,z \
	-v $(CURDIR)/packit_service:/usr/local/lib/python3.9/site-packages/packit_service:ro,z \
	-v $(CURDIR)/secrets/packit/dev/packit-service.yaml:/home/packit/.config/packit-service.yaml:ro,z \
	-v $(CURDIR)/secrets/packit/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
	-v $(CURDIR)/secrets/packit/dev/privkey.pem:/secrets/privkey.pem:ro,z \
	--network packit-service_default \
	quay.io/packit/packit-service:dev alembic revision -m "$(CHANGE)" --autogenerate
	$(COMPOSE) down # stop previously started pods: service, postgres and redis

check-db: compose-for-db-up
	sleep 10 # service pod have to be up and running and all migrations have to been applied
	$(CONTAINER_ENGINE) run --rm -ti \
		-e DEPLOYMENT=dev \
		-e REDIS_SERVICE_HOST=redis \
		-e POSTGRESQL_USER=packit \
		-e POSTGRESQL_PASSWORD=secret-password \
		-e POSTGRESQL_HOST=postgres \
		-e POSTGRESQL_DATABASE=packit \
		--env COV_REPORT \
		--env COLOR \
		-v $(CURDIR):/src:z \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml:z \
		-v $(CURDIR)/secrets/packit/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
		-v $(CURDIR)/secrets/packit/dev/privkey.pem:/secrets/privkey.pem:ro,z \
		-w /src \
		--network packit-service_default \
		$(SERVICE_IMAGE) make check "TEST_TARGET=tests_openshift/database tests_openshift/service"
		$(COMPOSE) down

regenerate-db-diagram: compose-for-db-up
	sleep 10
	mermerd -c postgresql://packit:secret-password@localhost:5432 -s public --useAllTables -o alembic/diagram.mmd
