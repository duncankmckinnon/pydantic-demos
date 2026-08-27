#!/bin/bash
set -euo pipefail

# Runs automatically on first boot of a fresh rx_assistant_db_data volume (Postgres's
# entrypoint executes every script in /docker-entrypoint-initdb.d once, in filename order,
# only when the data directory is empty). A .sql file here can't expand environment
# variables, so this is a shell script instead. POSTGRES_USER/POSTGRES_DB come from the
# rx-assistant-db service's existing `environment:` block; POSTGRESQL_USERNAME/PASSWORD
# come from the `env_file:` added in Step 4 below.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER "$POSTGRESQL_USERNAME" WITH PASSWORD '$POSTGRESQL_PASSWORD';
    GRANT pg_monitor TO "$POSTGRESQL_USERNAME";
EOSQL
