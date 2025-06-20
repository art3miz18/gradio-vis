# ocr_engine/Dockerfile
FROM python:3.9-slim

WORKDIR /app
# Install system dependencies (like poppler-utils)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    mupdf-tools \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/app
# Copy requirements and install Python packages
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy ALL application code from your local ocr_engine directory into /app in the container
COPY . /app

# The Celery command
# celery_app.py is now directly in /app, so celery_app:instance_name is correct
CMD ["celery", "-A", "celery_app:celery_ocr_engine_app", "worker", "-l", "info", "-c", "4", "--max-memory-per-child=300000", "--max-tasks-per-child=1"]
# Add -Q your_queue_name if you are using specific queues, e.g., -Q default_ocr_queue