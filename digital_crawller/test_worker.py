#!/usr/bin/env python3
"""
Simple test script to verify that the worker is functioning correctly.
"""

import os
import sys
import time
from datetime import datetime
from websitecrawler import IndianNewsCrawler
from newssites import TOP_INDIAN_NEWS_SITES
import math

def get_worker_sites(sites, total_workers, worker_id):
    """Distribute sites among workers"""
    sites_per_worker = math.ceil(len(sites) / total_workers)
    start_idx = worker_id * sites_per_worker
    end_idx = min(start_idx + sites_per_worker, len(sites))
    return sites[start_idx:end_idx]

def main():
    # Get worker configuration from environment
    worker_id = int(os.environ.get("WORKER_ID", "0"))
    total_workers = int(os.environ.get("TOTAL_WORKERS", "1"))
    max_threads = int(os.environ.get("MAX_THREADS", "10"))
    
    print(f"=== WORKER TEST ===")
    print(f"Worker ID: {worker_id}")
    print(f"Total Workers: {total_workers}")
    print(f"Max Threads: {max_threads}")
    print(f"Python Version: {sys.version}")
    print(f"Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get sites for this worker
    worker_sites = get_worker_sites(TOP_INDIAN_NEWS_SITES, total_workers, worker_id)
    print(f"Worker {worker_id + 1} assigned {len(worker_sites)} sites")
    
    # Print the first 5 sites
    print("First 5 sites:")
    for i, site in enumerate(worker_sites[:5]):
        print(f"  {i+1}. {site['name']} ({site['language']})")
    
    # Test crawler initialization
    try:
        print("\nInitializing crawler...")
        crawler = IndianNewsCrawler(
            base_output_dir="digital_data",
            max_threads=max_threads,
            use_direct_gateway=False
        )
        print("Crawler initialized successfully")
        
        # Test site folder creation
        if len(worker_sites) > 0:
            site_info = worker_sites[0]
            print(f"\nTesting site folder creation for {site_info['name']}...")
            site_folder, site_csv_path = crawler.create_site_folder(site_info['name'])
            print(f"Site folder created: {site_folder}")
            print(f"Site CSV created: {site_csv_path}")
        
        print("\nTest completed successfully")
        return 0
    except Exception as e:
        print(f"Error during test: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())