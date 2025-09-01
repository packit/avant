# Avant

Service to make onboarding packages to Fedora easier.
Development is currently taking place in the `service` branch.

## Workflow

##### Must have:

* [x] Forgejo integration (create projects and react to push events)
* [x] COPR builds
* [x] Run `packit` / `tmt` plans with Testing Farm on new packages
* [x] Reruns
* [x] Status reporting
* [x] Commit status

#### Testing
* [ ] ~~Unit tests~~ (see [packit/validation](https://github.com/packit/validation))
* [ ] OGR and Packit Service event tests

## Running locally

The setup is similar to [Packit Service](https://github.com/packit/packit-service/blob/main/CONTRIBUTING.md#running-packit-service-locally).

#### 1. Clone and prepare `ogr`

```bash
git clone https://github.com/mynk8/ogr
cd ogr
git checkout avant
```

#### 2. Build the `python3-ogr.rpm` package

```bash
# Build inside a Fedora 36 environment (toolbox can be used here).
packit build locally

# Copy the built package into the Avant files directory.
cp ./noarch/python3-*.rpm ~/avant/files/python3-ogr.rpm
```

#### 3. Build the container

```bash
docker compose build
```

> ⚠️ Make sure you have the correct setup for secrets and the `FORGEJO_TOKEN` environment variable configured in `docker-compose.yml`.

#### 4. Start the service

```bash
docker compose up
```

