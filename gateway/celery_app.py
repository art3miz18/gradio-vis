# gateway/celery_app.py
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0") # Default to localhost if not set

# This Celery app instance is for the gateway to send tasks
celery_gateway_app = Celery(
    "gateway_sender", # Can be any name, not critical as it doesn't define tasks
    broker=REDIS_URL,
    backend=REDIS_URL, # Backend is needed to retrieve task status/results via AsyncResult
    # include=[] # No tasks are defined in the gateway
)

celery_gateway_app.conf.update(
    task_ignore_result=False, # We want to be able to get results/status
    result_expires=3600,  # Keep results for 1 hour
    # Optional: if you want to enforce all tasks sent from here go to a specific queue by default
    # task_default_queue='pdf_processing_queue',
    # task_default_routing_key='pdf_processing_queue.#'
)

# Note: No task definitions here. This app is purely a client.