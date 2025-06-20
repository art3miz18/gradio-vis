import os
import time
import schedule
import subprocess
import pytz
from datetime import datetime
from websitecrawler import IndianNewsCrawler
import math
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description='News Crawler Scheduler')
    parser.add_argument('--worker-id', type=int, default=0,
                      help='Worker ID (default: 0)')
    parser.add_argument('--total-workers', type=int, default=1,
                      help='Total number of workers (default: 1)')
    parser.add_argument('--max-threads', type=int, default=20,
                      help='Maximum number of threads per worker (default: 20)')
    parser.add_argument('--min-articles', type=int, default=100,
                      help='Minimum articles per site (default: 100)')
    parser.add_argument('--max-sites', type=int, default=50,
                      help='Maximum sites to crawl (default: 50)')
    parser.add_argument('--use-direct-gateway', type=str, default='true',
                      help='Use direct gateway (default: true)')
    return parser.parse_args()

# Worker configuration
TOTAL_WORKERS = int(os.environ.get("TOTAL_WORKERS", "1"))
WORKER_ID = int(os.environ.get("WORKER_ID", "0"))

# Configure your daily run-time via env (HH:MM, 24h)
CRON_TIME = os.environ.get("CRON_TIME", "02:00")
# Set timezone for the scheduler to UTC
TIMEZONE = pytz.timezone('UTC')

# Cleanup configuration
CLEANUP_MODE = os.environ.get("CLEANUP_MODE", "full")  # full, selective, none
DAYS_TO_KEEP = os.environ.get("DAYS_TO_KEEP", "7")
OUTPUT_DIR = "digital_data"

# Crawler configuration
USE_DIRECT_GATEWAY = os.environ.get("USE_DIRECT_GATEWAY", "true").lower() == "true"
MAX_THREADS = int(os.environ.get("MAX_THREADS", "20"))
MIN_ARTICLES_PER_SITE = int(os.environ.get("MIN_ARTICLES_PER_SITE", "100"))
MAX_SITES = int(os.environ.get("MAX_SITES", "50"))
# Control whether to run immediately on startup or wait for scheduled time
RUN_ON_STARTUP = os.environ.get("RUN_ON_STARTUP", "false").lower() == "true"

def get_worker_sites(sites, total_workers, worker_id):
    """Distribute sites among workers"""
    sites_per_worker = math.ceil(len(sites) / total_workers)
    start_idx = worker_id * sites_per_worker
    end_idx = min(start_idx + sites_per_worker, len(sites))
    return sites[start_idx:end_idx]

def run_cleanup():
    """Run the cleanup script based on environment variables"""
    if CLEANUP_MODE != "none":
        print(f"[Scheduler] Running cleanup in {CLEANUP_MODE} mode")
        
        # Check if cleanup.sh exists (try both relative and absolute paths)
        cleanup_script = "./cleanup.sh"
        if not os.path.exists(cleanup_script):
            # Try absolute path
            cleanup_script = "/app/cleanup.sh"
            if not os.path.exists(cleanup_script):
                print(f"[Scheduler] Warning: {cleanup_script} not found. Performing manual cleanup.")
            
            # Manual cleanup implementation
            output_dir = "digital_data"
            if CLEANUP_MODE == "full":
                if os.path.exists(output_dir):
                    print(f"[Scheduler] Removing directory: {output_dir}")
                    import shutil
                    shutil.rmtree(output_dir, ignore_errors=True)
                else:
                    print(f"[Scheduler] Directory {output_dir} does not exist, nothing to clean")
            elif CLEANUP_MODE == "selective":
                if os.path.exists(output_dir):
                    import time
                    now = time.time()
                    days_to_keep = int(DAYS_TO_KEEP)
                    cutoff = now - (days_to_keep * 86400)
                    
                    print(f"[Scheduler] Removing files older than {days_to_keep} days")
                    for root, dirs, files in os.walk(output_dir, topdown=False):
                        for file in files:
                            file_path = os.path.join(root, file)
                            if os.path.getmtime(file_path) < cutoff:
                                os.remove(file_path)
                                print(f"[Scheduler] Removed: {file_path}")
                        
                        # Remove empty directories
                        if not os.listdir(root):
                            os.rmdir(root)
                            print(f"[Scheduler] Removed empty directory: {root}")
                else:
                    print(f"[Scheduler] Directory {output_dir} does not exist, nothing to clean")
            else:
                print(f"[Scheduler] Unknown cleanup mode: {CLEANUP_MODE}")
            
            return
        
        # Use the cleanup script if it exists
        try:
            if CLEANUP_MODE == "full":
                subprocess.run([cleanup_script, "full"], check=True)
            elif CLEANUP_MODE == "selective":
                subprocess.run([cleanup_script, "selective", DAYS_TO_KEEP], check=True)
            else:
                print(f"[Scheduler] Unknown cleanup mode: {CLEANUP_MODE}")
        except Exception as e:
            print(f"[Scheduler] Error running cleanup script: {e}")
            print("[Scheduler] Continuing with crawler execution...")


def run_crawl():
    print(f"[Scheduler] Starting crawl at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Scheduler] Worker {WORKER_ID + 1} of {TOTAL_WORKERS}")

    # Always run cleanup before crawling
    run_cleanup()

    # Create crawler with direct gateway sending if enabled
    crawler = IndianNewsCrawler(
        base_output_dir=OUTPUT_DIR,
        max_threads=MAX_THREADS,
        use_direct_gateway=USE_DIRECT_GATEWAY
    )
    
    # Retry any pending uploads first
    if hasattr(crawler, "retry_pending_uploads"):
        crawler.retry_pending_uploads()
    
    # Get sites for this worker
    from newssites import TOP_INDIAN_NEWS_SITES
    worker_sites = get_worker_sites(TOP_INDIAN_NEWS_SITES, TOTAL_WORKERS, WORKER_ID)
    
    # Start crawling
    print(f"[Scheduler] Crawling with settings: USE_DIRECT_GATEWAY={USE_DIRECT_GATEWAY}, MAX_SITES={MAX_SITES}, MIN_ARTICLES_PER_SITE={MIN_ARTICLES_PER_SITE}")
    print(f"[Scheduler] Worker {WORKER_ID + 1} assigned {len(worker_sites)} sites")
    
    # Override the sites list for this worker
    crawler.top_indian_news_sites = worker_sites
    crawler.crawl_all_sites(min_articles_per_site=MIN_ARTICLES_PER_SITE, max_sites=len(worker_sites))
    
    print(f"[Scheduler] Crawl finished at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("CRAWL COMPLETE")


if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()
    
    # Update environment variables from arguments
    os.environ["WORKER_ID"] = str(args.worker_id)
    os.environ["TOTAL_WORKERS"] = str(args.total_workers)
    os.environ["MAX_THREADS"] = str(args.max_threads)
    os.environ["MIN_ARTICLES_PER_SITE"] = str(args.min_articles)
    os.environ["MAX_SITES"] = str(args.max_sites)
    os.environ["USE_DIRECT_GATEWAY"] = args.use_direct_gateway
    
    # Schedule to run every 2 hours
    schedule.every(2).hours.do(run_crawl)
    
    # Calculate and display next run time
    next_run = schedule.next_run()
    # Make sure next_run has timezone info
    if next_run.tzinfo is None:
        next_run = TIMEZONE.localize(next_run)
    current_time = datetime.now(TIMEZONE)
    time_until_next_run = next_run - current_time
    
    print(f"[Scheduler] Worker {args.worker_id + 1} of {args.total_workers}")
    print(f"[Scheduler] Current time (UTC): {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"[Scheduler] Scheduled to run every 2 hours")
    print(f"[Scheduler] Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"[Scheduler] Time until next scheduled run: {time_until_next_run}")
    print(f"[Scheduler] Cleanup mode: {CLEANUP_MODE}")
    print(f"[Scheduler] RUN_ON_STARTUP: {RUN_ON_STARTUP}")
    if CLEANUP_MODE == "selective":
        print(f"[Scheduler] Will remove files older than {DAYS_TO_KEEP} days")

    # Only run immediately if RUN_ON_STARTUP is true
    if RUN_ON_STARTUP:
        print("[Scheduler] RUN_ON_STARTUP is enabled. Starting initial crawl run...")
        run_crawl()
        print("[Scheduler] Initial crawl completed. Waiting for next scheduled run.")
    else:
        print("[Scheduler] RUN_ON_STARTUP is disabled. Waiting for scheduled run time.")

    # Loop forever
    last_check = time.time()
    check_interval = 300  # Log status every 5 minutes
    
    while True:
        schedule.run_pending()
        
        # Log status periodically to confirm scheduler is running
        current_time = time.time()
        if current_time - last_check > check_interval:
            now = datetime.now(TIMEZONE)
            next_run_time = schedule.next_run()
            # Make sure next_run_time has timezone info
            if next_run_time.tzinfo is None:
                next_run_time = TIMEZONE.localize(next_run_time)
            time_until_next = next_run_time - now
            print(f"[Scheduler] Status check at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"[Scheduler] Next run scheduled for {next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"[Scheduler] Time until next run: {time_until_next}")
            last_check = current_time
            
        time.sleep(30)
