#!/usr/bin/env python3
"""
Test script for the direct gateway sending functionality in the digital crawler.
This script tests the DirectGatewaySender and the modified IndianNewsCrawler.
"""

import os
import sys
import json
import argparse
from datetime import datetime
from newsplease import NewsPlease
from websitecrawler import IndianNewsCrawler
from direct_gateway_sender import DirectGatewaySender

def test_direct_sender():
    """Test the DirectGatewaySender directly with a sample article"""
    print("\n=== Testing DirectGatewaySender ===")
    
    # Create a sender
    sender = DirectGatewaySender()
    
    # Example article data
    article_data = {
        "title": "Test Article from Direct Sender",
        "source": "Test Source",
        "url": "https://example.com/test-article",
        "date_published": datetime.now().strftime("%d-%m-%Y"),
        "authors": ["Test Author"],
        "language": "English",
        "content": "This is a test article content. It needs to be long enough to pass validation. " * 10,
        "category": "Test Category",
        "imagesUrls": ["https://example.com/test-image.jpg"],
        "originalClipUrls": ["https://example.com/test-article"]
    }
    
    # Send the article
    print("Sending test article to gateway...")
    result = sender.send_article_to_gateway(article_data)
    print(f"Result: {json.dumps(result, indent=2)}")
    
    return result["status"] == "success"

def test_crawler_with_url(url):
    """Test the IndianNewsCrawler with a specific URL"""
    print(f"\n=== Testing IndianNewsCrawler with URL: {url} ===")
    
    try:
        # Create a crawler with direct gateway sending enabled
        crawler = IndianNewsCrawler(use_direct_gateway=True)
        
        # Extract the site name from the URL
        site_name = url.split("//")[1].split(".")[0]
        if site_name == "www":
            site_name = url.split("//")[1].split(".")[1]
        
        print(f"Extracted site name: {site_name}")
        
        # Create site folder
        site_folder = crawler.create_site_folder(site_name)
        site_csv_path = os.path.join(site_folder, f"{site_name}_articles.csv")
        
        # Process the article
        print(f"Processing article from URL: {url}")
        result = crawler.process_article(url, site_name, site_folder, site_csv_path, "English")
        
        if result:
            print(f"Article processed successfully: {result}")
            return True
        else:
            print("Failed to process article")
            return False
    
    except Exception as e:
        print(f"Error testing crawler with URL: {e}")
        return False

def test_crawler_with_newsplease(url):
    """Test the IndianNewsCrawler with NewsPlease for a specific URL"""
    print(f"\n=== Testing IndianNewsCrawler with NewsPlease for URL: {url} ===")
    
    try:
        # Create a crawler with direct gateway sending enabled
        crawler = IndianNewsCrawler(use_direct_gateway=True)
        
        # Extract the site name from the URL
        site_name = url.split("//")[1].split(".")[0]
        if site_name == "www":
            site_name = url.split("//")[1].split(".")[1]
        
        print(f"Extracted site name: {site_name}")
        
        # Create site folder
        site_folder = crawler.create_site_folder(site_name)
        
        # Download and parse the article using NewsPlease
        print(f"Downloading article from URL: {url}")
        article = NewsPlease.from_url(url)
        
        if not article:
            print("Failed to download article")
            return False
        
        # Save the article using the modified save_article method
        print(f"Saving article: {article.title}")
        result = crawler.save_article(article, site_name, site_folder, "English")
        
        if result:
            print(f"Article saved successfully: {result}")
            return True
        else:
            print("Failed to save article")
            return False
    
    except Exception as e:
        print(f"Error testing crawler with NewsPlease: {e}")
        return False

def test_retry_pending_uploads():
    """Test the retry_pending_uploads functionality"""
    print("\n=== Testing retry_pending_uploads ===")
    
    try:
        # Create a crawler with direct gateway sending enabled
        crawler = IndianNewsCrawler(use_direct_gateway=True)
        
        # Retry pending uploads
        print("Retrying pending uploads...")
        crawler.retry_pending_uploads()
        
        return True
    
    except Exception as e:
        print(f"Error testing retry_pending_uploads: {e}")
        return False

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Test the direct gateway sending functionality")
    parser.add_argument("--url", help="URL of an article to test with", default="https://www.ndtv.com/india-news/pm-modi-to-visit-odisha-today-to-launch-projects-worth-rs-2-000-crore-5195839")
    parser.add_argument("--test-direct", action="store_true", help="Test the DirectGatewaySender directly")
    parser.add_argument("--test-crawler", action="store_true", help="Test the IndianNewsCrawler with a URL")
    parser.add_argument("--test-newsplease", action="store_true", help="Test the IndianNewsCrawler with NewsPlease")
    parser.add_argument("--test-retry", action="store_true", help="Test the retry_pending_uploads functionality")
    parser.add_argument("--test-all", action="store_true", help="Run all tests")
    
    args = parser.parse_args()
    
    # If no specific test is selected, run all tests
    if not (args.test_direct or args.test_crawler or args.test_newsplease or args.test_retry):
        args.test_all = True
    
    # Set environment variables for testing
    os.environ["GATEWAY_API_URL"] = os.environ.get("GATEWAY_API_URL", "http://localhost:5000")
    
    # Print test configuration
    print("=== Test Configuration ===")
    print(f"Gateway API URL: {os.environ['GATEWAY_API_URL']}")
    print(f"Test URL: {args.url}")
    
    # Run the selected tests
    results = {}
    
    if args.test_all or args.test_direct:
        results["direct_sender"] = test_direct_sender()
    
    if args.test_all or args.test_crawler:
        results["crawler"] = test_crawler_with_url(args.url)
    
    if args.test_all or args.test_newsplease:
        results["newsplease"] = test_crawler_with_newsplease(args.url)
    
    if args.test_all or args.test_retry:
        results["retry"] = test_retry_pending_uploads()
    
    # Print test results
    print("\n=== Test Results ===")
    for test, result in results.items():
        print(f"{test}: {'PASS' if result else 'FAIL'}")
    
    # Return success if all tests passed
    return all(results.values())

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)