#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
PGHOST="${PGHOST:-db}"
PGUSER="${POSTGRES_USER:?POSTGRES_USER is required}"
PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
PGDATABASE="${POSTGRES_DB:?POSTGRES_DB is required}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"

export PGPASSWORD

mkdir -p "$BACKUP_DIR"

DUMP_FILE="$BACKUP_DIR/gametrace-${STAMP}.dump"
COVERS_FILE="$BACKUP_DIR/covers-${STAMP}.tar.gz"

echo "Backing up PostgreSQL to ${DUMP_FILE}"
pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -Fc -f "$DUMP_FILE"

if [ -d /covers ] && [ -n "$(ls -A /covers 2>/dev/null || true)" ]; then
    echo "Backing up covers to ${COVERS_FILE}"
    tar -czf "$COVERS_FILE" -C /covers .
else
    echo "Skipping covers backup (volume empty or missing)"
fi

ln -sfn "$(basename "$DUMP_FILE")" "$BACKUP_DIR/gametrace-latest.dump"
if [ -f "$COVERS_FILE" ]; then
    ln -sfn "$(basename "$COVERS_FILE")" "$BACKUP_DIR/covers-latest.tar.gz"
fi

find "$BACKUP_DIR" -maxdepth 1 -name 'gametrace-*.dump' -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'covers-*.tar.gz' -mtime +"$RETENTION_DAYS" -delete

if [ -n "${BACKUP_MIRROR_DIR:-}" ]; then
    echo "Mirroring to ${BACKUP_MIRROR_DIR}"
    mkdir -p "$BACKUP_MIRROR_DIR"
    cp -a "$DUMP_FILE" "$BACKUP_MIRROR_DIR/"
    if [ -f "$COVERS_FILE" ]; then
        cp -a "$COVERS_FILE" "$BACKUP_MIRROR_DIR/"
    fi
    ln -sfn "$(basename "$DUMP_FILE")" "$BACKUP_MIRROR_DIR/gametrace-latest.dump"
    if [ -f "$COVERS_FILE" ]; then
        ln -sfn "$(basename "$COVERS_FILE")" "$BACKUP_MIRROR_DIR/covers-latest.tar.gz"
    fi
    find "$BACKUP_MIRROR_DIR" -maxdepth 1 -name 'gametrace-*.dump' -mtime +"$RETENTION_DAYS" -delete
    find "$BACKUP_MIRROR_DIR" -maxdepth 1 -name 'covers-*.tar.gz' -mtime +"$RETENTION_DAYS" -delete
fi

echo "Backup complete: ${DUMP_FILE}"