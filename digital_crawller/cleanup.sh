#!/bin/bash
# cleanup.sh - Script to clean up the crawler data directory

# Configuration
OUTPUT_DIR="digital_data"
CLEANUP_MODE=${1:-"full"}  # Options: full, selective
DAYS_TO_KEEP=${2:-7}       # Only used in selective mode

echo "Starting cleanup of $OUTPUT_DIR"

if [ "$CLEANUP_MODE" = "full" ]; then
    echo "Performing full cleanup (removing entire directory)"
    if [ -d "$OUTPUT_DIR" ]; then
        rm -rf "$OUTPUT_DIR"
        echo "Directory $OUTPUT_DIR removed"
    else
        echo "Directory $OUTPUT_DIR does not exist, nothing to clean"
    fi
elif [ "$CLEANUP_MODE" = "selective" ]; then
    echo "Performing selective cleanup (files older than $DAYS_TO_KEEP days)"
    if [ -d "$OUTPUT_DIR" ]; then
        find "$OUTPUT_DIR" -type f -mtime +$DAYS_TO_KEEP -exec rm {} \;
        echo "Removed files older than $DAYS_TO_KEEP days"
        
        # Optional: Remove empty directories
        find "$OUTPUT_DIR" -type d -empty -delete
        echo "Removed empty directories"
    else
        echo "Directory $OUTPUT_DIR does not exist, nothing to clean"
    fi
else
    echo "Unknown cleanup mode: $CLEANUP_MODE"
    echo "Usage: ./cleanup.sh [full|selective] [days_to_keep]"
    exit 1
fi

echo "Cleanup completed"
