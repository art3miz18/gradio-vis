# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a microservices-based newspaper OCR and content analysis system that processes PDFs and extracts structured content using AI analysis. The system consists of multiple containerized services working together to handle newspaper processing workflows.

## Core Architecture

### Service Components
- **Gateway Service** (`gateway/`): FastAPI-based API gateway handling HTTP requests and task orchestration
- **OCR Engine** (`ocr_engine/`): Celery worker processing PDFs, performing OCR, and AI content analysis
- **Gradio UI** (`ui/` and root `gradio_interface.py`): Web interface for manual PDF uploads and processing
- **Redis**: Message broker for Celery task queue
- **Flower**: Celery task monitoring dashboard

### Processing Flows
1. **Dashboard PDF Upload**: Manual uploads via Gradio UI → Gateway API → OCR processing
2. **Crawler PDF Upload**: Automated newspaper crawler → Gateway API → OCR processing → Node.js callback
3. **Digital Article Processing**: JSON article data → AI analysis → Node.js callback
4. **Direct Image Processing**: Image directory processing for newspaper pages
5. **Raw JSON Processing**: Direct article text analysis

## Development Commands

### Running the Full Stack
```bash
# Development environment with hot reloading
docker compose -f docker-compose.epaper-test.yml up --build

# Access services:
# - Gateway API: http://localhost:5001
# - Gradio UI: http://localhost:7860
# - Flower dashboard: http://localhost:5055
# - Redis: localhost:6379
```

### Gradio UI Versions
- **Primary**: `ui/gradio_interface.py` - Enhanced dashboard with full visualization (requires Gradio 4.0+)
- **Fallback**: `ui/gradio_interface_v3_compatible.py` - Simplified version for Gradio 3.50.2 compatibility
- **Root**: `gradio_interface.py` - Basic interface for standalone testing

### Individual Service Development
```bash
# Gateway service only
cd gateway && uvicorn main:app --host 0.0.0.0 --port 5001 --reload

# OCR worker only (requires Redis running)
cd ocr_engine && celery -A celery_app:celery_ocr_engine_app worker -l info -c 4

# Gradio UI standalone
python gradio_interface.py
```

### Testing and Monitoring
```bash
# Check Celery worker health
docker exec -it <ocr_container> celery -A celery_app:celery_ocr_engine_app inspect ping

# Monitor task logs
docker logs -f <ocr_container>

# Access Flower dashboard for task monitoring
# http://localhost:5055 (credentials in .env file)
```

## Key Configuration

### Environment Variables Required
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: S3 storage credentials
- `AWS_S3_BUCKET_NAME`: S3 bucket for storing processed content
- `GOOGLE_API_KEY`: Google Gemini API key for content analysis
- `NEWSPAPER_SEGMENTATION_API_KEY`: Arcanum API key for newspaper segmentation
- `REDIS_URL`: Redis connection string (default: redis://redis:6379/0)
- `CRAWLER_API_URL`: Node.js callback endpoint for processed content
- `FLOWER_USER`, `FLOWER_PASS`: Flower dashboard authentication
- `ADMIN_TOKEN`: Admin API authentication token

### Service Dependencies
- **Gateway**: Depends on Redis for task queuing
- **OCR Engine**: Requires Redis, S3 access, Google Gemini API, Arcanum API
- **Gradio UI**: Connects to Gateway service
- **Flower**: Monitors Redis and Celery workers

## Code Architecture Details

### Gateway Service (`gateway/`)
- FastAPI application in `main.py` with REST endpoints
- Celery client configuration in `celery_app.py` for task dispatching
- No actual task processing - delegates to OCR engine workers
- Handles file uploads, parameter validation, and task status queries

### OCR Engine (`ocr_engine/`)
- **Task Definitions** (`tasks.py`): Celery tasks for different processing flows
- **Pipeline Logic** (`pipeline_logic.py`): Core PDF→image→OCR→analysis workflow
- **Services Directory**: Modular processing components
  - `pdf_converter.py`: PDF to image conversion using mutool
  - `image_processor.py`: Image cropping and processing
  - `content_analyzer.py`: AI-powered content analysis using Gemini
  - `s3_handler.py`: AWS S3 upload/download operations
- **Configuration** (`config.py`): Environment setup, API clients, model initialization
- **Models** (`models.py`): Pydantic data models for request/response structures

### Gradio Interface
- Multiple interface files: root `gradio_interface.py`, `ui/gradio_interface.py`, `scripts/gradio_interface.py`
- Provides tabs for PDF upload, image processing, and custom prompt testing
- Connects to Gateway API endpoints for task submission

## Processing Pipeline Details

1. **PDF Upload** → Gateway receives file and metadata
2. **Task Dispatch** → Gateway creates Celery task for OCR engine
3. **PDF Conversion** → OCR engine converts PDF to images using mutool
4. **Page Segmentation** → Arcanum API segments newspaper pages into articles
5. **Article Extraction** → Images cropped based on segmentation data
6. **OCR Processing** → Text extraction from article images
7. **AI Analysis** → Gemini API analyzes content for metadata extraction
8. **Storage** → Results uploaded to S3 storage
9. **Callback** → Optional Node.js callback with processed data

## API Endpoints

### Gateway Service
- `POST /pipeline`: Upload PDF from dashboard
- `POST /crawl/newspaper_pdf`: Upload PDF from crawler
- `POST /process/digital_s3_json`: Process digital article from S3
- `POST /process/digital_raw_json`: Process raw article JSON
- `POST /process/direct_images`: Process image directory
- `POST /process/single_image`: Process single newspaper page
- `GET /tasks/{task_id}`: Query task status
- `POST /admin/update_prompt`: Update AI analysis prompt (requires admin token)

## Development Notes

- Celery workers use memory management: `--max-memory-per-child=300000 --max-tasks-per-child=1`
- AI prompt templates are in `ocr_engine/config_newPrompt.py`
- Shared volume `newspaper_images` for image storage between services
- Network configuration uses external `ml_default` network
- OCR engine supports both English and regional language newspapers
- System designed for horizontal scaling with multiple Celery workers