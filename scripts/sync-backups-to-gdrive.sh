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
echo "Verified ${#archives[@]} encrypted Daily Podium archives on Google Drive."
