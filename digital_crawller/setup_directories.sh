#!/bin/bash
# setup_directories.sh - Create the required directories for the digital crawler

# Create the failed_requests directory
mkdir -p failed_requests
chmod 777 failed_requests
echo "Created failed_requests directory"

# Create the digital_data directory
mkdir -p digital_data
chmod 777 digital_data
echo "Created digital_data directory"

echo "Directories created successfully"