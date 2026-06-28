#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PGHOST="${PGHOST:-db}"
PGUSER="${POSTGRES_USER:?POSTGRES_USER is required}"
PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
PGDATABASE="${POSTGRES_DB:?POSTGRES_DB is required}"

DUMP_FILE="${1:-$BACKUP_DIR/gametrace-latest.dump}"
COVERS_FILE="${2:-$BACKUP_DIR/covers-latest.tar.gz}"

if [ -L "$DUMP_FILE" ]; then
    DUMP_FILE="$BACKUP_DIR/$(basename "$(readlink "$DUMP_FILE")")"
fi
if [ -L "$COVERS_FILE" ]; then
    COVERS_FILE="$BACKUP_DIR/$(basename "$(readlink "$COVERS_FILE")")"
fi

export PGPASSWORD

if [ ! -f "$DUMP_FILE" ]; then
    echo "Dump file not found: ${DUMP_FILE}" >&2
    exit 1
fi

echo "Restoring PostgreSQL from ${DUMP_FILE}"
pg_restore -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" --clean --if-exists --no-owner --no-acl "$DUMP_FILE"

if [ -f "$COVERS_FILE" ]; then
    echo "Restoring covers from ${COVERS_FILE}"
    mkdir -p /covers
    tar -xzf "$COVERS_FILE" -C /covers
else
    echo "No covers archive at ${COVERS_FILE} — skipping"
fi

echo "Restore complete"