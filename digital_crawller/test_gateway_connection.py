#!/usr/bin/env python3
"""
Test script to verify that the digital crawler can connect to the gateway.
"""

import os
import sys
import requests
import time

# Get the gateway URL from environment variable
GATEWAY_API_URL = os.environ.get("GATEWAY_API_URL", "http://gateway:5000")

def test_gateway_connection():
    """Test the connection to the gateway"""
    print(f"Testing connection to gateway: {GATEWAY_API_URL}")
    
    # Try to connect to the gateway
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

def main():
    """Main function"""
    print("=== Gateway Connection Test ===")
    print(f"Gateway API URL: {GATEWAY_API_URL}")
    
    # Try to connect to the gateway with retries
    max_retries = 5
    retry_delay = 5
    
    for i in range(max_retries):
        print(f"Attempt {i+1}/{max_retries}...")
        if test_gateway_connection():
            print("Gateway connection test passed!")
            return 0
        
        if i < max_retries - 1:
            print(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
    
    print("Gateway connection test failed after multiple attempts.")
    return 1

if __name__ == "__main__":
    sys.exit(main())