# Newspaper Scraper & OCR Service

A comprehensive microservices-based system for crawling newspaper websites, processing PDFs, and extracting structured content using OCR and AI analysis.

## 🌟 Overview

This project provides an end-to-end solution for:

1. **Crawling newspaper websites** to download PDF editions
2. **Processing PDF documents** through OCR and segmentation
3. **Analyzing content** with AI to extract meaningful information
4. **Storing and delivering** processed content through a unified API

The system is built as a set of microservices using Docker containers, making it scalable and deployable in various environments.

## 🏗️ Architecture

The system consists of the following components:

### 1. Gateway Service
- FastAPI-based API gateway
- Handles incoming requests and file uploads
- Dispatches tasks to the OCR engine via Celery
- Provides status tracking for processing tasks

### 2. OCR Engine
- Processes PDF documents and images
- Segments newspaper pages into individual articles
- Performs OCR on article images
- Analyzes content using AI (Google's Gemini API)
- Extracts metadata like ministry names, sentiment, etc.

### 3. Newspaper Crawler
- Scrapes various newspaper websites
- Downloads PDF editions
- Uploads PDFs to S3 storage
- Triggers processing via the Gateway API

### 4. Supporting Services
- **Redis**: Message broker for Celery tasks
- **Flower**: Monitoring dashboard for Celery tasks
- **S3 Storage**: For storing PDFs, images, and processed data

## 🔄 Processing Flows

The system supports three main processing flows:

### Flow 1: Dashboard PDF Upload
- Manual PDF upload through a dashboard
- PDF is processed and articles are extracted
- Results are stored in S3

### Flow 2: Crawler PDF Upload
- Automated PDF download by the crawler
- PDF is processed and articles are extracted
- Results are stored in S3 and sent to a Node.js callback endpoint

### Flow 3: Digital Article Processing
- JSON data from digital news articles is processed
- Content is analyzed using AI
- Results are sent to a Node.js callback endpoint

## 🚀 Getting Started

### Prerequisites

- Docker and Docker Compose
- AWS S3 account (for storage)
- Google Cloud account (for Gemini API)
- Arcanum API key (for newspaper segmentation)

### Environment Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd newspaper_scarapper_ocr_service
   ```

2. Create environment files:
   - `.env` - Main environment variables
   - `.env.Newscrawler` - Crawler-specific variables

   Example `.env` file:
   ```
   # AWS S3 Configuration
   AWS_ACCESS_KEY_ID=your_access_key
   AWS_SECRET_ACCESS_KEY=your_secret_key
   AWS_S3_BUCKET_NAME=your_bucket_name
   AWS_S3_REGION=ap-south-1
   
   # Redis Configuration
   REDIS_URL=redis://redis:6379/0
   
   # Crawler API Callback
   CRAWLER_API_URL=http://your-callback-api/endpoint
   
   # Flower Dashboard
   FLOWER_USER=admin
   FLOWER_PASS=secure_password
   
   # Google Gemini API
   GOOGLE_API_KEY=your_gemini_api_key
   
   # Arcanum Segmentation API
   SEGMENTATION_API_KEY=your_arcanum_api_key
   ```

### Development Setup

Run the development environment:

```bash
docker-compose up --build
```

This will start all services with development configurations, including hot-reloading for code changes.

### Production Deployment

For production deployment:

```bash
docker-compose -f docker-compose.prod.yml up -d
```

The production configuration uses pre-built images and includes Docker Swarm settings for scaling.

## 📡 API Endpoints

### Gateway API

- **POST /pipeline**
  - Upload PDF from dashboard
  - Parameters: `pdf`, `publicationName`, `editionName`, `languageName`, `zoneName`, `date`, `dpi`, `quality`, `resize_bool`

- **POST /crawl/newspaper_pdf**
  - Upload PDF from crawler
  - Parameters: `pdf`, `publicationName`, `editionName`, `languageName`, `date`, `zoneName`, `dpi`, `quality`, `resize_bool`

- **POST /process/digital_s3_json**
  - Process digital article from S3 JSON
  - Parameters: `s3_url`, `site_name`, `timestamp`, `mediaId`

- **POST /process/single_image**
  - Process a single newspaper page image
  - Parameters: `image`, `publicationName`, `editionName`, `languageName`, `zoneName`, `date`, `pageNumber`

- **GET /tasks/{task_id}**
  - Check status of a processing task
  - Returns task state and results

## 🔧 Configuration

### OCR Engine Configuration

Key configuration options for the OCR engine:

- **DPI**: Controls the resolution of extracted images (default: 200)
- **Quality**: JPEG quality for extracted images (default: 85)
- **Resize**: Whether to resize large images (default: true)

### Crawler Configuration

The crawler can be configured to target specific newspaper websites. Currently supported newspapers include:

- The Sentinel
- Telangana Today
- Sandesh
- Rising Kashmir
- Rajasthan Patrika
- Kashmir Monitor
- Haribhoomi
- Dainik Jagran
- And more...

## 🛠️ Development

### Project Structure

```
newspaper_scarapper_ocr_service/
├── crawller_newspapers/     # Newspaper crawler service
│   ├── src/                 # Crawler scripts for different newspapers
│   └── requirements.txt     # Crawler dependencies
├── gateway/                 # API Gateway service
│   ├── main.py              # FastAPI application
│   ├── celery_app.py        # Celery configuration
│   ├── Dockerfile           # Gateway container definition
│   └── requirements.txt     # Gateway dependencies
├── ocr_engine/              # OCR processing service
│   ├── services/            # Core services (PDF conversion, image processing, etc.)
│   ├── utils/               # Utility functions
│   ├── celery_app.py        # Celery worker configuration
│   ├── config.py            # Configuration settings
│   ├── models.py            # Data models
│   ├── pipeline_logic.py    # Main processing pipeline
│   ├── tasks.py             # Celery task definitions
│   ├── Dockerfile           # OCR engine container definition
│   └── requirements.txt     # OCR engine dependencies
├── docker-compose.yml       # Development environment definition
└── docker-compose.prod.yml  # Production environment definition
```

### Adding New Newspaper Sources

To add support for a new newspaper:

1. Create a new crawler script in `crawller_newspapers/src/`
2. Implement the download and upload logic
3. Test the crawler with the Gateway API

## 📊 Monitoring

The system includes Flower for monitoring Celery tasks:

- Access the Flower dashboard at `http://localhost:5055`
- Login with the credentials defined in the `.env` file

## 🔒 Security Considerations

- All API keys and credentials should be stored in environment variables
- S3 bucket permissions should be properly configured
- Consider implementing API authentication for the Gateway service
- Regularly update dependencies to address security vulnerabilities


## 🎛️ Local Gradio Interface

This repository includes an optional Gradio-based client for interacting with the Gateway API.

### Prerequisites

Install Gradio and Requests in your Python environment:

```bash
pip install gradio requests
```

### Running the Interface

Launch the UI by running:

```bash
python gradio_interface.py
```

The interface allows you to send requests to the Gateway service running on `http://localhost:8000` by default. Set the `GATEWAY_URL` environment variable to target a different gateway instance.

### Usage Examples

**Upload a PDF**
1. Select the *Upload PDF* tab.
2. Choose a PDF file and fill in the publication information.
3. Click **Submit PDF**. The response contains a `task_id` you can query via `/tasks/{task_id}`.

**Process Images**
1. Open the *Direct Images* tab.
2. Enter the path to the directory containing images on the gateway host.
3. Click **Submit Images** to queue the `/process/direct_images` task.

**Send Raw JSON**
1. Use the *Raw JSON* tab to paste article text.
2. Press **Submit JSON** to call `/process/digital_raw_json`.

### Modifying Prompts

Prompt templates used for article analysis live in `ocr_engine/config_newPrompt.py`. Edit the `*_SYSTEM_INSTRUCTION` strings to customize Gemini behavior and restart the OCR workers for changes to take effect.

## ➕ New API Endpoints

Alongside existing routes, the Gateway now exposes:

- **POST /process/direct_images** – queue OCR processing for a local image directory. Returns `{ "message": str, "task_id": str }`.
- **POST /process/digital_raw_json** – analyze article content sent directly as JSON. Returns `{ "message": str, "task_id": str }`.

These complement `/pipeline`, `/crawl/newspaper_pdf`, and `/process/digital_s3_json`. Retrieve task progress via `GET /tasks/{task_id}` which reports the Celery state and results.
