#!/usr/bin/env python3
"""
Crawler Monitoring Script

This script monitors the status of multiple crawler workers and provides statistics
on their performance, including sites crawled, articles processed, and errors.

Usage:
    python monitor_crawlers.py
"""

import os
import json
import time
import glob
import argparse
import datetime
import logging
from collections import defaultdict
from typing import Dict, List, Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("crawler_monitor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("crawler_monitor")

def parse_args():
    parser = argparse.ArgumentParser(description='Crawler Monitor')
    parser.add_argument('--data-dir', type=str, default='digital_data',
                      help='Directory containing crawler data (default: digital_data)')
    parser.add_argument('--failed-dir', type=str, default='failed_requests',
                      help='Directory containing failed requests (default: failed_requests)')
    parser.add_argument('--interval', type=int, default=300,
                      help='Monitoring interval in seconds (default: 300)')
    return parser.parse_args()

def count_files_by_extension(directory: str) -> Dict[str, int]:
    """Count files by extension in a directory"""
    if not os.path.exists(directory):
        return {}
    
    counts = defaultdict(int)
    for root, _, files in os.walk(directory):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            counts[ext] += 1
    
    return dict(counts)

def get_latest_files(directory: str, extension: str = '.json', count: int = 5) -> List[str]:
    """Get the latest files with a specific extension"""
    if not os.path.exists(directory):
        return []
    
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if filename.endswith(extension):
                file_path = os.path.join(root, filename)
                files.append((file_path, os.path.getmtime(file_path)))
    
    # Sort by modification time (newest first) and return the paths
    return [f[0] for f in sorted(files, key=lambda x: x[1], reverse=True)[:count]]

def get_site_statistics(data_dir: str) -> Dict[str, Any]:
    """Get statistics on crawled sites"""
    if not os.path.exists(data_dir):
        return {}
    
    site_counts = defaultdict(int)
    article_counts = defaultdict(int)
    total_articles = 0
    
    # Find all JSON files (article metadata)
    json_files = glob.glob(os.path.join(data_dir, '**', '*.json'), recursive=True)
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                site_name = data.get('source_name', 'Unknown')
                site_counts[site_name] += 1
                total_articles += 1
        except Exception as e:
            logger.error(f"Error reading {json_file}: {e}")
    
    # Sort sites by article count (descending)
    sorted_sites = sorted(site_counts.items(), key=lambda x: x[1], reverse=True)
    
    return {
        'total_articles': total_articles,
        'total_sites': len(site_counts),
        'sites_by_count': dict(sorted_sites),
        'top_sites': dict(sorted_sites[:10])
    }

def get_failed_requests(failed_dir: str) -> Dict[str, Any]:
    """Get statistics on failed requests"""
    if not os.path.exists(failed_dir):
        return {'total_failed': 0}
    
    failed_files = glob.glob(os.path.join(failed_dir, '**', '*.json'), recursive=True)
    failed_count = len(failed_files)
    
    error_types = defaultdict(int)
    sites_with_errors = defaultdict(int)
    
    for failed_file in failed_files:
        try:
            with open(failed_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                error_type = data.get('error_type', 'Unknown')
                site_name = data.get('site_name', 'Unknown')
                error_types[error_type] += 1
                sites_with_errors[site_name] += 1
        except Exception as e:
            logger.error(f"Error reading failed request {failed_file}: {e}")
    
    return {
        'total_failed': failed_count,
        'error_types': dict(error_types),
        'sites_with_errors': dict(sites_with_errors)
    }

def get_worker_logs() -> Dict[str, Any]:
    """Get information from worker logs"""
    log_files = glob.glob('crawler_worker_*.log')
    
    worker_status = {}
    for log_file in log_files:
        worker_id = log_file.replace('crawler_worker_', '').replace('.log', '')
        
        try:
            # Get the last 10 lines of the log file
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                last_lines = lines[-10:] if len(lines) >= 10 else lines
                
                # Extract the last timestamp
                last_timestamp = None
                for line in reversed(lines):
                    if ' - INFO - ' in line:
                        timestamp_str = line.split(' - INFO - ')[0]
                        try:
                            last_timestamp = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                            break
                        except:
                            pass
                
                worker_status[worker_id] = {
                    'last_activity': last_timestamp.strftime('%Y-%m-%d %H:%M:%S') if last_timestamp else 'Unknown',
                    'recent_logs': last_lines
                }
        except Exception as e:
            logger.error(f"Error reading log file {log_file}: {e}")
    
    return worker_status

def monitor_crawlers(data_dir: str, failed_dir: str, interval: int):
    """Main monitoring function"""
    while True:
        try:
            # Get current time
            current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Get file counts
            file_counts = count_files_by_extension(data_dir)
            
            # Get site statistics
            site_stats = get_site_statistics(data_dir)
            
            # Get failed requests
            failed_stats = get_failed_requests(failed_dir)
            
            # Get worker logs
            worker_logs = get_worker_logs()
            
            # Print summary
            print("\n" + "="*80)
            print(f"CRAWLER MONITOR REPORT - {current_time}")
            print("="*80)
            
            print("\nFILE STATISTICS:")
            print(f"Total JSON files: {file_counts.get('.json', 0)}")
            print(f"Total HTML files: {file_counts.get('.html', 0)}")
            print(f"Total TXT files: {file_counts.get('.txt', 0)}")
            
            print("\nARTICLE STATISTICS:")
            print(f"Total articles: {site_stats.get('total_articles', 0)}")
            print(f"Total sites: {site_stats.get('total_sites', 0)}")
            
            print("\nTOP 10 SITES:")
            for site, count in site_stats.get('top_sites', {}).items():
                print(f"  {site}: {count} articles")
            
            print("\nFAILED REQUESTS:")
            print(f"Total failed: {failed_stats.get('total_failed', 0)}")
            
            if failed_stats.get('error_types'):
                print("\nERROR TYPES:")
                for error_type, count in failed_stats.get('error_types', {}).items():
                    print(f"  {error_type}: {count}")
            
            print("\nWORKER STATUS:")
            for worker_id, status in worker_logs.items():
                print(f"  Worker {worker_id} - Last activity: {status.get('last_activity', 'Unknown')}")
            
            print("\nLATEST ARTICLES:")
            latest_files = get_latest_files(data_dir, '.json', 5)
            for file_path in latest_files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        print(f"  {data.get('title', 'Unknown')} - {data.get('source_name', 'Unknown')}")
                except:
                    print(f"  {os.path.basename(file_path)}")
            
            print("\n" + "="*80)
            
            # Wait for the next check
            time.sleep(interval)
            
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    args = parse_args()
    print(f"Starting crawler monitor (checking every {args.interval} seconds)")
    print(f"Data directory: {args.data_dir}")
    print(f"Failed requests directory: {args.failed_dir}")
    
    monitor_crawlers(args.data_dir, args.failed_dir, args.interval)