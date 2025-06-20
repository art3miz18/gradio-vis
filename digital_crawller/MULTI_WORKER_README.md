# Multi-Worker Digital Crawler

This document explains how the multi-worker digital crawler system works to cover more news sites every 2 hours.

## Overview

The multi-worker system uses Docker containers to run multiple crawler instances in parallel. Each worker is assigned a unique subset of news sites to crawl, ensuring no duplication of effort.

## Key Components

1. **Improved Scheduler** (`scheduler_improved.py`)
   - Distributes sites among workers
   - Runs every 2 hours with staggered start times
   - Avoids multiprocessing issues in Docker

2. **Multi-Worker Dockerfile** (`Dockerfile.multi`)
   - Configurable via environment variables
   - Includes test script to verify worker functionality

3. **Docker Compose Configuration** (`docker-compose.crawler-multi.yml`)
   - Runs multiple worker containers
   - Each worker has a unique ID and assigned sites

## How It Works

1. **Site Distribution**: The `get_worker_sites` function divides the list of news sites among workers:
   ```python
   def get_worker_sites(sites, total_workers, worker_id):
       sites_per_worker = math.ceil(len(sites) / total_workers)
       start_idx = worker_id * sites_per_worker
       end_idx = min(start_idx + sites_per_worker, len(sites))
       return sites[start_idx:end_idx]
   ```

2. **Sequential Processing**: Each worker processes its assigned sites sequentially to avoid multiprocessing issues in Docker.

3. **Staggered Scheduling**: Workers run every 2 hours, with start times staggered based on worker ID to avoid overwhelming news sites.

## Configuration

Each worker can be configured with these environment variables:

- `WORKER_ID`: Unique ID for this worker (0-based)
- `TOTAL_WORKERS`: Total number of workers in the system
- `MAX_THREADS`: Maximum number of threads per worker
- `MIN_ARTICLES_PER_SITE`: Minimum articles to crawl per site
- `MAX_SITES`: Maximum number of sites to crawl
- `USE_DIRECT_GATEWAY`: Whether to use direct gateway sending
- `RUN_ON_STARTUP`: Whether to run immediately on startup

## Deployment

To deploy the multi-worker system:

```bash
docker-compose -f docker-compose.crawler-multi.yml up -d
```

To check the logs:

```bash
docker-compose -f docker-compose.crawler-multi.yml logs -f
```

## Troubleshooting

If workers get stuck, check:

1. **Logs**: Look for errors in the worker logs
2. **Test Script**: Run the test script to verify worker functionality
   ```bash
   docker exec -it crawller-new_digital_crawler_worker1_1 python test_worker.py
   ```
3. **Memory Usage**: Ensure containers have enough memory
4. **Network Connectivity**: Verify workers can connect to news sites and the gateway