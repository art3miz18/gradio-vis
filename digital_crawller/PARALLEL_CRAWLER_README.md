# Parallel Digital Crawler System

This document explains the parallel digital crawler system that runs multiple crawler workers to cover more news sites every 2 hours.

## Overview

The parallel crawler system uses multiple worker containers to distribute the crawling workload. Each worker is responsible for a subset of the news sites, allowing the system to process more content in parallel while reducing the load on individual sites.

## Key Components

1. **Multi-Worker Dockerfile** (`Dockerfile.multi`)
   - Configurable worker settings via environment variables
   - Optimized for parallel processing

2. **Improved Scheduler** (`scheduler_improved.py`)
   - Staggered start times to prevent all workers hitting sites simultaneously
   - Better logging and error handling
   - Configurable via environment variables

3. **Multi-Worker Docker Compose** (`docker-compose.crawler-multi.yml`)
   - Runs 4 crawler workers in parallel
   - Each worker handles a different subset of sites
   - Shared volumes for data storage

4. **Crawler Monitor** (`monitor_crawlers.py`)
   - Monitors the status of all crawler workers
   - Provides statistics on crawled sites and articles
   - Tracks failed requests and errors

## How It Works

1. The system divides the list of news sites among the workers (e.g., with 4 workers, each handles ~25% of the sites)
2. Workers run on a staggered schedule every 2 hours to avoid overwhelming the sites
3. Each worker processes its assigned sites in parallel using multiple threads
4. Results are stored in a shared volume accessible by all workers
5. The monitor script provides real-time statistics on the crawling process

## Configuration

### Worker Settings

Each worker can be configured with the following environment variables:

- `WORKER_ID`: Unique ID for this worker (0-based)
- `TOTAL_WORKERS`: Total number of workers in the system
- `MAX_THREADS`: Maximum number of threads per worker
- `MIN_ARTICLES_PER_SITE`: Minimum articles to crawl per site
- `MAX_SITES`: Maximum number of sites to crawl
- `USE_DIRECT_GATEWAY`: Whether to use direct gateway sending
- `RUN_ON_STARTUP`: Whether to run immediately on startup

### Scheduling

Workers run every 2 hours, with staggered start times based on their worker ID:

- Worker 0: XX:00
- Worker 1: XX:10
- Worker 2: XX:20
- Worker 3: XX:30

This prevents all workers from hitting the sites at the same time.

## Deployment

### Using Docker Compose

To deploy the multi-worker system:

```bash
docker-compose -f docker-compose.crawler-multi.yml up -d
```

### Monitoring

To monitor the crawler workers:

```bash
python monitor_crawlers.py
```

## Performance Considerations

- **Memory Usage**: Each worker uses approximately 200-300MB of RAM
- **CPU Usage**: Peak CPU usage during crawling is about 1-2 cores per worker
- **Network**: Workers make many HTTP requests, ensure adequate bandwidth
- **Storage**: Crawled data is stored in the shared volume, monitor disk usage

## Troubleshooting

### Common Issues

1. **Worker not starting**: Check the logs for errors
   ```bash
   docker logs digital_crawler_worker1
   ```

2. **Workers crawling the same sites**: Verify worker IDs are unique and total workers is set correctly

3. **High memory usage**: Reduce the number of threads per worker or increase the container memory limit

4. **Network errors**: Some sites may block crawlers, check the failed requests directory

### Logs

Each worker writes logs to:
- `crawler_worker_X.log` (where X is the worker ID)

The monitor writes logs to:
- `crawler_monitor.log`

## Scaling

To scale to more or fewer workers:

1. Update the `TOTAL_WORKERS` environment variable for all workers
2. Add or remove worker services in the docker-compose file
3. Ensure each worker has a unique `WORKER_ID`

## Best Practices

1. **Respect robots.txt**: The crawler respects robots.txt by default
2. **Rate limiting**: Workers use random delays between requests to avoid overwhelming sites
3. **Error handling**: Failed requests are stored for later analysis
4. **Monitoring**: Use the monitor script to track performance and detect issues