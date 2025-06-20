import os
import json
import requests
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("direct_gateway_sender.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Gateway API URL
GATEWAY_API_URL = os.environ.get("GATEWAY_API_URL", "http://gateway:5000")
DIGITAL_RAW_JSON_ENDPOINT = f"{GATEWAY_API_URL}/process/digital_raw_json"

class DirectGatewaySender:
    """
    Class to send digital article data directly to the gateway without using S3.
    """
    
    def __init__(self):
        """Initialize the DirectGatewaySender."""
        self.gateway_url = DIGITAL_RAW_JSON_ENDPOINT
        logger.info(f"DirectGatewaySender initialized with gateway URL: {self.gateway_url}")
        
        # Create a directory to store failed requests for retry
        self.failed_requests_dir = "failed_requests"
        os.makedirs(self.failed_requests_dir, exist_ok=True)
    
    def send_article_to_gateway(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send article data directly to the gateway.
        
        Args:
            article_data: Dictionary containing article data
            
        Returns:
            Dictionary with status and response information
        """
        try:
            # Ensure required fields are present
            if "content" not in article_data or not article_data["content"]:
                logger.error(f"Article content is missing or empty: {article_data.get('title', 'Untitled')}")
                return {"status": "error", "message": "Article content is missing or empty"}
            
            # Add timestamp if not present
            if "crawled_on" not in article_data:
                article_data["crawled_on"] = datetime.now().isoformat()
            
            # Add media ID if not present
            if "mediaId" not in article_data:
                article_data["mediaId"] = 2  # Default media ID for digital news
            
            # Send the request to the gateway
            logger.info(f"Sending article to gateway: {article_data.get('title', 'Untitled')}")
            response = requests.post(
                self.gateway_url,
                json=article_data,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            # Check if the request was successful
            if response.status_code == 202:
                response_data = response.json()
                logger.info(f"Article sent successfully: {response_data}")
                return {
                    "status": "success",
                    "message": "Article sent successfully",
                    "task_id": response_data.get("task_id"),
                    "response": response_data
                }
            else:
                # Save the failed request for retry
                self._save_failed_request(article_data, response.status_code, response.text)
                logger.error(f"Failed to send article: {response.status_code} - {response.text}")
                return {
                    "status": "error",
                    "message": f"Failed to send article: {response.status_code}",
                    "response": response.text
                }
        
        except Exception as e:
            # Save the failed request for retry
            self._save_failed_request(article_data, 0, str(e))
            logger.error(f"Error sending article: {e}")
            return {"status": "error", "message": f"Error sending article: {e}"}
    
    def _save_failed_request(self, article_data: Dict[str, Any], status_code: int, error_message: str) -> None:
        """
        Save a failed request for later retry.
        
        Args:
            article_data: The article data that failed to send
            status_code: The HTTP status code of the failed request
            error_message: The error message
        """
        try:
            # Create a unique filename
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"{self.failed_requests_dir}/failed_{timestamp}_{article_data.get('title', 'untitled')[:20].replace(' ', '_')}.json"
            
            # Save the failed request data
            with open(filename, "w", encoding="utf-8") as f:
                json.dump({
                    "article_data": article_data,
                    "status_code": status_code,
                    "error_message": error_message,
                    "timestamp": timestamp
                }, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Failed request saved to {filename}")
        
        except Exception as e:
            logger.error(f"Error saving failed request: {e}")
    
    def retry_failed_requests(self) -> Dict[str, int]:
        """
        Simplified retry function that just logs the number of failed requests.
        
        Returns:
            Dictionary with counts of failed requests
        """
        try:
            # Simplified retry function that just logs the number of failed request files
            failed_files = [f for f in os.listdir(self.failed_requests_dir) if f.startswith("failed_") and f.endswith(".json")]
            
            if not failed_files:
                logger.info("No failed requests found")
                return {"success": 0, "failure": 0}
            
            logger.info(f"Found {len(failed_files)} failed requests")
            
            # Just log the number of failed requests without retrying
            return {"success": 0, "failure": len(failed_files)}
        
        except Exception as e:
            logger.error(f"Error checking failed requests: {e}")
            return {"success": 0, "failure": 0, "error": str(e)}


# Example usage
if __name__ == "__main__":
    # Create a sender
    sender = DirectGatewaySender()
    
    # Example article data
    article_data = {
        "title": "Test Article",
        "source": "Test Source",
        "url": "https://example.com/test-article",
        "date_published": datetime.now().strftime("%d-%m-%Y"),
        "authors": ["Test Author"],
        "language": "English",
        "content": "This is a test article content. It needs to be long enough to pass validation.",
        "category": "Test Category",
        "imagesUrls": ["https://example.com/test-image.jpg"],
        "originalClipUrls": ["https://example.com/test-article"]
    }
    
    # Send the article
    result = sender.send_article_to_gateway(article_data)
    print(f"Result: {result}")
    
    # Retry any failed requests
    retry_result = sender.retry_failed_requests()
    print(f"Retry result: {retry_result}")