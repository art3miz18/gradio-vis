import os
import time
import json
import redis
import asyncio
import logging
import requests
from celery import Celery
from typing import Dict, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("ocr_bridge.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Redis connection
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# Celery app
celery_app = Celery(
    'ocr_bridge',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Gateway API endpoint
GATEWAY_URL = os.getenv('GATEWAY_URL', 'http://gateway:8000')

async def process_image_and_submit_to_pipeline(job_data: Dict[str, Any]):
    """
    Process an image and submit it to the OCR pipeline
    """
    try:
        # Extract data
        shared_path = job_data.get('shared_path')
        publication_info = job_data.get('publication_info', {})
        
        if not shared_path or not os.path.exists(shared_path):
            logger.error(f"File not found at shared path: {shared_path}")
            return {'error': 'File not found'}
        
        # Determine file type and endpoint
        file_ext = os.path.splitext(shared_path)[1].lower()
        
        # Choose the appropriate endpoint based on file type
        if file_ext in ['.pdf']:
            endpoint = f"{GATEWAY_URL}/crawl/newspaper_pdf"
            
            # Prepare the form data for PDF files
            with open(shared_path, 'rb') as f:
                files = {'pdf': f}
                data = {
                    'publicationName': publication_info.get('publicationName', 'Unknown'),
                    'editionName': publication_info.get('editionName', ''),
                    'languageName': publication_info.get('languageName', 'English'),
                    'zoneName': publication_info.get('zoneName', ''),
                    'date': time.strftime('%d-%m-%Y'),
                    'dpi': 200,
                    'quality': 85,
                    'resize_bool': 'true'
                }
                
                # Submit to gateway
                response = await asyncio.to_thread(
                    requests.post,
                    endpoint,
                    files=files,
                    data=data
                )
        
        elif file_ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
            # For image files, we'll use a different approach
            # First, create a task directly using Celery
            
            # Upload the image to a temporary location
            temp_dir = '/tmp/ocr_bridge_temp'
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = os.path.join(temp_dir, os.path.basename(shared_path))
            
            # Copy the file
            with open(shared_path, 'rb') as src, open(temp_path, 'wb') as dst:
                dst.write(src.read())
            
            # Create a task for the OCR engine to process this image
            result = celery_app.send_task(
                'ocr_engine.process_image',
                args=[
                    temp_path,
                    publication_info.get('publicationName', 'Unknown'),
                    publication_info.get('editionName', ''),
                    publication_info.get('languageName', 'English'),
                    publication_info.get('zoneName', '')
                ],
                kwargs={
                    'dpi': 200,
                    'quality': 85
                }
            )
            
            # Return the task ID
            return {
                'status': 'task_created',
                'task_id': result.id,
                'temp_path': temp_path
            }
        
        else:
            logger.error(f"Unsupported file type: {file_ext}")
            return {'error': f"Unsupported file type: {file_ext}"}
        
        # Check response for gateway submission
        if response.status_code == 200 or response.status_code == 202:
            return response.json()
        else:
            logger.error(f"Error submitting to gateway: {response.status_code}, {response.text}")
            return {'error': f"Gateway error: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"Error processing image and submitting to pipeline: {e}")
        return {'error': str(e)}

async def listen_for_processed_images():
    """
    Listen for processed images from the image processor service
    """
    # Create Redis pubsub
    pubsub = redis_client.pubsub()
    pubsub.subscribe('ocr_results')
    
    logger.info("OCR bridge service started, listening for processed images...")
    
    # Listen for messages
    try:
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    # Parse result data
                    result_data = json.loads(message['data'])
                    job_id = result_data.get('job_id', 'unknown')
                    logger.info(f"Received processing result for job: {job_id}")
                    
                    # If there's no error, submit to OCR pipeline
                    if 'error' not in result_data:
                        # Get the original job data
                        job_data_str = redis_client.hget('ocr_jobs', job_id)
                        if job_data_str:
                            job_data = json.loads(job_data_str)
                            
                            # Submit to pipeline
                            pipeline_result = await process_image_and_submit_to_pipeline(job_data)
                            
                            # Log result
                            if 'error' in pipeline_result:
                                logger.error(f"Pipeline submission for job {job_id} failed: {pipeline_result['error']}")
                            else:
                                logger.info(f"Pipeline submission for job {job_id} successful: {pipeline_result}")
                            
                            # Store result
                            redis_client.hset('pipeline_results', job_id, json.dumps(pipeline_result))
                        else:
                            logger.error(f"Original job data not found for job ID: {job_id}")
                    else:
                        logger.error(f"Processing error for job {job_id}: {result_data['error']}")
                
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in message: {message['data']}")
                except Exception as e:
                    logger.error(f"Error processing result: {e}")
    
    except Exception as e:
        logger.error(f"Error in pubsub listener: {e}")

if __name__ == "__main__":
    # Run the listener
    asyncio.run(listen_for_processed_images())