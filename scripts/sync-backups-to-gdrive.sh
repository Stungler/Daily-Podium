#!/usr/bin/env bash
set -euo pipefail

backup_dir=/home/stefan/backups/daily-podium
remote=gdrive:daily-podium-backups

shopt -s nullglob
archives=("$backup_dir"/daily-podium-*.tar.gz.enc)
if ((${#archives[@]} == 0)); then
  echo "No verified encrypted Daily Podium archives to upload." >&2
  exit 1
fi

# Copy does not delete older offsite versions; immutable prevents overwrites.
rclone copy "$backup_dir" "$remote" \
  --include 'daily-podium-*.tar.gz.enc' --checksum --immutable --max-duration 20m
rclone check "$backup_dir" "$remote" \
  --include 'daily-podium-*.tar.gz.enc' --one-way --max-duration 20m
marker_dir=/home/stefan/dev/Daily-Podium/data
marker=$(mktemp "$marker_dir/.offsite-last-success.XXXXXX")
trap 'rm -f "$marker"' EXIT
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$marker"
mv "$marker" "$marker_dir/offsite-last-success.txt"
echo "Verified ${#archives[@]} encrypted Daily Podium archives on Google Drive."
