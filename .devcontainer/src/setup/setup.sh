#!/bin/sh
# One-shot DB initialization (docker-compose "setup" service, later a Kubernetes Job).
set -eu

export PGHOST="${DB_HOST:-postgresqldb}"
export PGPORT="${DB_PORT:-5432}"
export PGDATABASE="${DB_NAME:?DB_NAME is not set}"
export PGUSER="${DB_USER:?DB_USER is not set}"
export PGPASSWORD="${DB_PASSWORD:?DB_PASSWORD is not set}"

echo "Waiting for database ${PGHOST}:${PGPORT}/${PGDATABASE} ..."
until pg_isready -q; do
    sleep 2
done

echo "Creating schema ..."
psql -v ON_ERROR_STOP=1 -f /opt/setup/create_schema.sql

if [ "${DB_SEED:-true}" = "true" ]; then
    echo "Populating schema with sample data ..."
    psql -v ON_ERROR_STOP=1 -f /opt/setup/populate_schema.sql
fi

echo "Database setup finished."
