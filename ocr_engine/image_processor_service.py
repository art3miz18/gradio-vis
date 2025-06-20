import os
import json
import time
import redis
import logging
import requests
import asyncio
from typing import Dict, Any, List
import base64
from io import BytesIO
from PIL import Image

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("image_processor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Redis connection
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# Shared volume path
SHARED_VOLUME_PATH = os.getenv('SHARED_VOLUME_PATH', '/app/shared_data')

# Configuration for ML endpoint
ML_ENDPOINT = os.getenv('ML_ENDPOINT')
if not ML_ENDPOINT:
    logger.warning("ML_ENDPOINT not configured. Images will be processed but not sent to ML service.")

# Configuration for cleanup
# Set to 'true' to delete files after processing
AUTO_CLEANUP = os.getenv('AUTO_CLEANUP', 'false').lower() == 'true'

async def process_image(image_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process an image file
    """
    try:
        # Check if the file exists
        if not os.path.exists(image_path):
            logger.error(f"Image file not found: {image_path}")
            return {'error': 'Image file not found'}
        
        # Read image
        with Image.open(image_path) as img:
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Get image info
            width, height = img.size
            
            # Create response object
            result = {
                'file_path': image_path,
                'width': width,
                'height': height,
                'file_size': os.path.getsize(image_path),
                'publication_info': metadata.get('publication_info', {}),
                'processed_timestamp': time.time()
            }
            
            # If ML endpoint is configured, send the image there
            if ML_ENDPOINT and ML_ENDPOINT.lower() != 'null':
                # Convert image to base64 for API request if needed
                buffered = BytesIO()
                img.save(buffered, format="JPEG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                
                # Prepare the payload
                publication_info = metadata.get('publication_info', {})
                payload = {
                    'image': img_base64,
                    'publicationName': publication_info.get('publicationName', ''),
                    'editionName': publication_info.get('editionName', ''),
                    'languageName': publication_info.get('languageName', ''),
                    'zoneName': publication_info.get('zoneName', '')
                }
                
                # Send to ML service asynchronously
                async def send_to_ml():
                    try:
                        response = await asyncio.to_thread(
                            requests.post,
                            ML_ENDPOINT,
                            json=payload,
                            timeout=30
                        )
                        if response.status_code == 200:
                            return response.json()
                        else:
                            logger.error(f"ML service responded with status {response.status_code}")
                            return {'error': f"ML service error: {response.status_code}"}
                    except Exception as e:
                        logger.error(f"Error sending to ML service: {e}")
                        return {'error': f"ML service exception: {str(e)}"}
                
                # Wait for ML service response
                ml_response = await send_to_ml()
                result['ml_response'] = ml_response
            
            # Return result
            return result
    
    except Exception as e:
        logger.error(f"Error processing image {image_path}: {e}")
        return {'error': str(e)}

async def process_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a job from the Redis queue
    """
    try:
        # Get file information
        shared_path = job_data.get('shared_path')
        if not shared_path:
            logger.error(f"Shared path not found in job data: {job_data}")
            return {'error': 'Shared path not found'}
        
        # Check if file exists
        if not os.path.exists(shared_path):
            logger.error(f"File not found at shared path: {shared_path}")
            return {'error': 'File not found at shared path'}
        
        # Process the file based on file type
        file_type = job_data.get('file_type', '').lower()
        
        if file_type in ['jpg', 'jpeg', 'png', 'tiff', 'bmp']:
            # Process image directly
            result = await process_image(shared_path, job_data)
        else:
            # Unsupported file type
            logger.error(f"Unsupported file type: {file_type}")
            result = {'error': f"Unsupported file type: {file_type}"}
        
        # Clean up if auto cleanup is enabled
        if AUTO_CLEANUP and 'error' not in result:
            try:
                os.remove(shared_path)
                logger.info(f"Cleaned up file: {shared_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {shared_path}: {e}")
        
        # Return result
        return result
    
    except Exception as e:
        logger.error(f"Error processing job: {e}")
        return {'error': str(e)}

async def listen_for_jobs():
    """
    Listen for image processing jobs from Redis
    """
    # Create Redis pubsub
    pubsub = redis_client.pubsub()
    pubsub.subscribe('ocr_jobs')
    
    logger.info("Image processor service started, listening for jobs...")
    
    # First check if there are any jobs in the queue
    while True:
        try:
            # Check for jobs in the queue
            queued_job = redis_client.rpop('ocr_job_queue')
            if queued_job:
                # Parse job data
                try:
                    job_data = json.loads(queued_job)
                    logger.info(f"Processing queued job: {job_data.get('job_id', 'unknown')}")
                    
                    # Process the job
                    result = await process_job(job_data)
                    
                    # Log result
                    if 'error' in result:
                        logger.error(f"Job {job_data.get('job_id', 'unknown')} failed: {result['error']}")
                    else:
                        logger.info(f"Job {job_data.get('job_id', 'unknown')} processed successfully")
                    
                    # Store result in Redis
                    redis_client.hset('ocr_job_results', job_data.get('job_id', 'unknown'), json.dumps(result))
                    
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in queued job: {queued_job}")
                except Exception as e:
                    logger.error(f"Error processing queued job: {e}")
            else:
                # No more jobs in queue, break and start listening
                break
        except Exception as e:
            logger.error(f"Error checking queue: {e}")
            break
    
    # Listen for new jobs
    try:
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    # Parse job data
                    job_data = json.loads(message['data'])
                    logger.info(f"Received job: {job_data.get('job_id', 'unknown')}")
                    
                    # Process the job asynchronously
                    asyncio.create_task(process_job(job_data))
                    
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in message: {message['data']}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
    except Exception as e:
        logger.error(f"Error in pubsub listener: {e}")

if __name__ == "__main__":
    # Run the listener
    asyncio.run(listen_for_jobs())