#!/usr/bin/env python3
"""
Test script to verify the full flow from digital crawler to gateway to OCR engine.
This script:
1. Tests the connection to the gateway
2. Sends a test article to the gateway
3. Checks the task status in the OCR engine
"""

import os
import sys
import json
import time
import requests
import argparse
from datetime import datetime
from direct_gateway_sender import DirectGatewaySender

# Get the gateway URL from environment variable
GATEWAY_API_URL = os.environ.get("GATEWAY_API_URL", "http://gateway:5000")

def test_gateway_connection():
    """Test the connection to the gateway"""
    print(f"Testing connection to gateway: {GATEWAY_API_URL}")
    
    try:
        response = requests.get(f"{GATEWAY_API_URL}/health", timeout=5)
        if response.status_code == 200:
            print(f"✅ Successfully connected to gateway: {GATEWAY_API_URL}")
            print(f"Response: {response.text}")
            return True
        else:
            print(f"❌ Failed to connect to gateway: {GATEWAY_API_URL}")
            print(f"Status code: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Error connecting to gateway: {e}")
        return False

def send_test_article():
    """Send a test article to the gateway"""
    print("\n=== Sending Test Article to Gateway ===")
    
    # Create a sender
    sender = DirectGatewaySender()
    
    # Example article data
    article_data = {
        "title": "Test Article from Full Flow Test",
        "source": "Test Source",
        "url": "https://example.com/test-article",
        "date_published": datetime.now().strftime("%d-%m-%Y"),
        "authors": ["Test Author"],
        "language": "English",
        "content": "This is a test article content for the full flow test. " + 
                  "It needs to be long enough to pass validation. " * 10 +
                  "The article discusses various government initiatives and " +
                  "mentions the Ministry of Education and Ministry of Health.",
        "category": "Test Category",
        "imagesUrls": ["https://example.com/test-image.jpg"],
        "originalClipUrls": ["https://example.com/test-article"]
    }
    
    # Send the article
    print("Sending test article to gateway...")
    result = sender.send_article_to_gateway(article_data)
    print(f"Result: {json.dumps(result, indent=2)}")
    
    return result

def check_task_status(task_id):
    """Check the status of a task in the OCR engine"""
    print(f"\n=== Checking Task Status: {task_id} ===")
    
    max_retries = 10
    retry_delay = 5
    
    for i in range(max_retries):
        try:
            response = requests.get(f"{GATEWAY_API_URL}/tasks/{task_id}", timeout=5)
            if response.status_code == 200:
                status_data = response.json()
                print(f"Task status: {status_data.get('state')}")
                
                if status_data.get('state') == "SUCCESS":
                    print("✅ Task completed successfully!")
                    print(f"Result: {json.dumps(status_data.get('result'), indent=2)}")
                    return True
                elif status_data.get('state') == "FAILURE":
                    print("❌ Task failed!")
                    print(f"Error: {status_data.get('result')}")
                    return False
                else:
                    print(f"Task still in progress: {status_data.get('state')}")
            else:
                print(f"❌ Failed to get task status: {response.status_code}")
                print(f"Response: {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Error checking task status: {e}")
        
        if i < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
    
    print("❌ Task did not complete within the expected time.")
    return False

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Test the full flow from digital crawler to gateway to OCR engine")
    parser.add_argument("--skip-gateway-test", action="store_true", help="Skip the gateway connection test")
    parser.add_argument("--skip-task-check", action="store_true", help="Skip checking the task status")
    
    args = parser.parse_args()
    
    print("=== Full Flow Test ===")
    print(f"Gateway API URL: {GATEWAY_API_URL}")
    
    # Test the connection to the gateway
    if not args.skip_gateway_test:
        if not test_gateway_connection():
            print("❌ Gateway connection test failed. Exiting.")
            return 1
    
    # Send a test article to the gateway
    result = send_test_article()
    
    if result["status"] != "success":
        print("❌ Failed to send test article to gateway. Exiting.")
        return 1
    
    # Check the task status
    if not args.skip_task_check and "task_id" in result:
        task_id = result["task_id"]
        if not check_task_status(task_id):
            print("❌ Task did not complete successfully. Exiting.")
            return 1
    
    print("\n=== Full Flow Test Completed Successfully ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())