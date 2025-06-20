# ocr_engine/celery_app.py
import os
from celery import Celery
from celery.signals import worker_process_init

@worker_process_init.connect(weak=False) # weak=False ensures it's not garbage collected
def celery_worker_process_init(**kwargs):
    pid = os.getpid()
    print(f"Celery worker process {pid} initializing...")
    # Import config functions here to avoid circular dependencies at module load time if config imports celery_app
    from config import assign_gemini_key_and_configure_sdk, init_models_for_process
    if assign_gemini_key_and_configure_sdk():
        init_models_for_process()
    else:
        print(f"Celery worker process {pid}: Failed to assign/configure Gemini key. Gemini calls may fail.")


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_ocr_engine_app = Celery(
    "ocr_engine_worker_app",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks']  # Tells Celery to load tasks.py from the same directory
)
celery_ocr_engine_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    worker_send_task_events=True,  # <<< ENABLE WORKER EVENTS
    task_send_sent_event=True,
)