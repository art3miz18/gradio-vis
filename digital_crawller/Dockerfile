FROM python:3.10-slim

WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev \
    curl \
    build-essential \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY websitecrawler.py /app/
COPY direct_gateway_sender.py /app/
COPY test_direct_gateway.py /app/
COPY test_gateway_connection.py /app/
COPY test_full_flow.py /app/
COPY .env /app/
COPY scheduler.py /app/
COPY newssites.py /app/

# Copy and make scripts executable
COPY cleanup.sh /app/cleanup.sh
COPY run_tests.sh /app/run_tests.sh
COPY create_cleanup_script.sh /app/create_cleanup_script.sh
RUN chmod +x /app/cleanup.sh /app/run_tests.sh /app/create_cleanup_script.sh && \
    dos2unix /app/cleanup.sh /app/run_tests.sh /app/create_cleanup_script.sh

# Make test scripts executable
RUN chmod +x test_direct_gateway.py test_gateway_connection.py test_full_flow.py

# Environment variables for cleanup configuration
ENV CLEANUP_MODE="full"
# ENV DAYS_TO_KEEP="7"

# Create output directories
RUN mkdir -p /app/digital_data /app/failed_requests && \
    chmod -R 777 /app/digital_data /app/failed_requests

# Create a startup script
RUN echo '#!/bin/bash' > /app/start.sh && \
    echo 'echo "Current directory: $(pwd)"' >> /app/start.sh && \
    echo 'echo "Listing files in current directory:"' >> /app/start.sh && \
    echo 'ls -la' >> /app/start.sh && \
    echo 'echo "Listing files in /app directory:"' >> /app/start.sh && \
    echo 'ls -la /app' >> /app/start.sh && \
    echo 'echo "Running create_cleanup_script.sh..."' >> /app/start.sh && \
    echo 'bash ./create_cleanup_script.sh' >> /app/start.sh && \
    echo 'echo "Starting scheduler..."' >> /app/start.sh && \
    echo 'python scheduler.py' >> /app/start.sh && \
    chmod +x /app/start.sh

# Run the startup script with bash to ensure proper execution
CMD ["bash", "/app/start.sh"] 