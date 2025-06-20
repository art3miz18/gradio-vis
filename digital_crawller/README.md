# Indian News Web Crawler

A containerized web crawler designed to extract articles from major Indian news websites in multiple languages.

## Features

- Crawls 50+ Indian news websites in multiple languages (English, Hindi, Tamil, etc.)
- Scheduled daily runs with configurable timing
- Automatic data cleanup to manage storage
- Direct gateway integration for immediate processing
- AWS S3 integration for cloud storage (fallback)
- Automatic retry of failed requests
- Containerized for easy deployment

## Requirements

- Docker
- AWS account (optional, for S3 storage)

## Quick Start

### 1. Clone the repository

```bash
git clone <repository-url>
cd web-crawler
```

### 2. Configure environment variables

Create or modify the `.env` file:

```
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
REGION_NAME=ap-south-1
BUCKET_NAME=your-bucket-name
ML_ENDPOINT=http://your-ml-endpoint
CRON_SCHEDULE=02:00
```

### 3. Build and run the Docker container

```bash
docker build -t indian-news-crawler .
docker run -d --name news-crawler indian-news-crawler
```

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| CRON_TIME | Daily schedule time (HH:MM, 24h format) | 02:00 |
| CLEANUP_MODE | Data cleanup strategy (full, selective, none) | full |
| DAYS_TO_KEEP | Days to keep data when using selective cleanup | 7 |
| AWS_ACCESS_KEY_ID | AWS access key for S3 storage | - |
| AWS_SECRET_ACCESS_KEY | AWS secret key for S3 storage | - |
| REGION_NAME | AWS region for S3 bucket | ap-south-1 |
| BUCKET_NAME | S3 bucket name for storage | - |
| GATEWAY_API_URL | URL of the gateway API | http://gateway:5000 |
| S3_NOTIFY_ENDPOINT | Endpoint for S3 notifications | http://gateway:5000/process/digital_raw_json |
| USE_DIRECT_GATEWAY | Whether to use direct gateway sending | true |
| MAX_THREADS | Maximum number of threads for crawling | 10 |
| MIN_ARTICLES_PER_SITE | Minimum number of articles to crawl per site | 50 |
| MAX_SITES | Maximum number of sites to crawl | 20 |

### Crawler Settings

Edit `scheduler.py` to modify these parameters:

```python
crawler.crawl_all_sites(min_articles_per_site=200, max_sites=50)
```

- `min_articles_per_site`: Minimum number of articles to crawl per site
- `max_sites`: Maximum number of sites to crawl

## Data Storage

Crawled articles are stored in the `digital_data` directory with the following structure:

```
digital_data/
  ├── site_name_1/
  │   ├── article_1.json
  │   ├── article_2.json
  │   └── ...
  ├── site_name_2/
  │   └── ...
  └── ...
```

## Customizing News Sources

Edit `newssites.py` to add or remove news sources:

```python
TOP_INDIAN_NEWS_SITES = [
    {"name": "Site Name", "url": "https://example.com", "language": "Language"},
    # Add more sites here
]
```

## Running Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Run the crawler
python scheduler.py
```

## Testing

### Using the Test Suite

The digital crawler comes with a comprehensive test suite that you can run using the `run_tests.sh` script:

```bash
# Run all tests
./run_tests.sh

# Run a specific test
./run_tests.sh gateway   # Test gateway connection
./run_tests.sh direct    # Test direct gateway sender
./run_tests.sh crawler   # Test crawler with URL
./run_tests.sh newsplease # Test crawler with NewsPlease
./run_tests.sh retry     # Test retry functionality
./run_tests.sh full      # Test full flow
```

### Using Docker

You can run the tests inside the Docker container:

```bash
docker-compose exec digital_crawler ./run_tests.sh
```

### Individual Test Scripts

You can also run the individual test scripts directly:

```bash
# Test the gateway connection
python test_gateway_connection.py

# Test the DirectGatewaySender
python test_direct_gateway.py --test-direct

# Test the IndianNewsCrawler with a specific URL
python test_direct_gateway.py --test-crawler --url https://www.example.com/article

# Test the IndianNewsCrawler with NewsPlease for a specific URL
python test_direct_gateway.py --test-newsplease --url https://www.example.com/article

# Test the retry_pending_uploads functionality
python test_direct_gateway.py --test-retry

# Test the full flow from crawler to gateway to OCR engine
python test_full_flow.py
```

## Cleanup Script

The cleanup script can be run manually:

```bash
# Full cleanup (remove all data)
./cleanup.sh full

# Selective cleanup (remove data older than X days)
./cleanup.sh selective 7
```

## Troubleshooting

### Failed Requests

If sending to the gateway fails, the article data is saved in the `failed_requests` directory. These requests will be retried automatically the next time the crawler runs.

You can manually retry failed requests using the `retry_pending_uploads` method:

```python
from websitecrawler import IndianNewsCrawler

crawler = IndianNewsCrawler(use_direct_gateway=True)
crawler.retry_pending_uploads()
```

### Common Issues

1. **Cleanup script not found**: If you see an error about `cleanup.sh` not being found, the script might not have been properly copied to the container or might not have the correct permissions. You can fix this by:
   ```bash
   # Inside the container
   touch /app/cleanup.sh
   chmod +x /app/cleanup.sh
   echo '#!/bin/bash' > /app/cleanup.sh
   echo 'echo "Cleanup script running"' >> /app/cleanup.sh
   ```

2. **Gateway connection issues**: If the crawler can't connect to the gateway, check that:
   - The gateway service is running
   - The `GATEWAY_API_URL` environment variable is set correctly
   - The network between the crawler and gateway is working
   - Run `python test_gateway_connection.py` to diagnose connection issues

3. **OCR engine not processing articles**: If articles are being sent to the gateway but not processed by the OCR engine, check that:
   - The OCR engine service is running
   - The Celery workers are running
   - Redis is running and accessible
   - Check the OCR engine logs for errors

4. **Docker volume permissions**: If you're seeing permission issues with volumes, make sure the directories exist and have the correct permissions:
   ```bash
   mkdir -p ./digital_crawller/failed_requests
   chmod 777 ./digital_crawller/failed_requests
   ```