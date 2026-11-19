.PHONY: lint typecheck format check fix test test-postgres up down backup backup-check require-version release release-dry-run

# Check lint and formatting
lint:
	uv run ruff check .
	uv run ruff format --check .
	npx --yes prettier@3.9.6 --check .
	$(MAKE) typecheck

typecheck:
	uv run pyright

# Alias for lint
check: lint

# Autofix lint issues and reformat
format:
	uv run ruff check --fix .
	uv run ruff format .
	npx --yes prettier@3.9.6 --write .

# Alias for format
fix: format

test:
	uv run --group dev pytest -q

# The service tests too, on the local compose Postgres (make up first)
test-postgres:
	PLYSTORE_TEST_DATABASE_URL=postgresql://plystore:plystore@127.0.0.1:55432 uv run --group dev pytest -q

# The local Postgres and service (docker/compose.yml); down keeps the data volume
up:
	docker compose -f docker/compose.yml up -d --build

down:
	docker compose -f docker/compose.yml down

POSTGRES ?= plystore-postgres-1
BACKUPS ?= backups

# Dump every database of the local compose Postgres into $(BACKUPS) (scripts/backup.sh; never removes old dumps)
backup:
	scripts/backup.sh $(POSTGRES) $(BACKUPS)

# The restore check: dump plybench, restore it into a new database, compare the two, then drop that copy
backup-check:
	scripts/backup.sh $(POSTGRES) $(BACKUPS) plybench
	scripts/restore.sh $(POSTGRES) $$(ls -t $(BACKUPS)/plybench-*.dump | head -1) plybench_restore_check
	scripts/compare.sh $(POSTGRES) plybench plybench_restore_check; status=$$?; docker exec $(POSTGRES) dropdb -U plystore plybench_restore_check; exit $$status

require-version:
	@test -n "$(VERSION)" || (echo "usage: make $(MAKECMDGOALS) VERSION=0.1.0" >&2; exit 1)

# Cut a release: make release VERSION=0.1.0
# Bumps the version, tags it, and publishes a GitHub Release, which triggers the PyPI upload. The service tests run in CI (on its Postgres).
release: require-version lint test
	uv run python scripts/release.py $(VERSION)

# Print every step of a release without changing anything
release-dry-run: require-version
	uv run python scripts/release.py $(VERSION) --dry-run
