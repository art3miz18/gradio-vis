# gateway/main.py
import os
import uuid
import re
from typing import Optional, Any, List, Dict

import boto3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from pydantic import BaseModel, Field

from celery_app import celery_gateway_app

app = FastAPI()

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "ok", "service": "gateway"}

# --- AWS S3 Config ---
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_S3_REGION", "ap-south-1")
s3_client = None
if AWS_S3_BUCKET_NAME and os.getenv('AWS_ACCESS_KEY_ID') and os.getenv('AWS_SECRET_ACCESS_KEY'):
    s3_client = boto3.client(
        "s3", region_name=AWS_REGION,
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )
    print("Gateway S3 client configured.")
else:
    print("⚠️ WARNING (Gateway): S3 credentials/bucket incomplete.")

# --- Pydantic Models for Gateway ---
class TaskResponse(BaseModel): task_id: str; message: Optional[str] = None
class SimpleAckResponse(BaseModel): message: str; task_id: Optional[str] = None
class StatusResponse(BaseModel): state: str; result: Optional[Dict[str, Any]] = None; info: Optional[Any] = None

# --- CORRECTED INPUT MODEL for Flow 3 (/process/digital_s3_json) ---
# This is what the crawler sends TO THE GATEWAY for Flow 3
class DigitalS3JsonPayloadFromCrawler(BaseModel):
    s3_url: str # URL of the JSON file in S3 containing the article details
    site_name: Optional[str] = None # Fallback publication name if not in S3 JSON's "source"
    timestamp: Optional[str] = None # Crawler's timestamp for this event
    mediaId: int = Field(default=2, description="Media ID for this source (e.g., 2 for digital eNews)")

# --- NEW MODEL for Flow 5 (/process/digital_raw_json) ---
# This is what the crawler sends TO THE GATEWAY for direct JSON processing
class DigitalRawJsonPayload(BaseModel):
    title: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    date_published: Optional[str] = None
    authors: Optional[List[str]] = Field(default_factory=list)
    language: Optional[str] = None
    crawled_on: Optional[str] = None
    content: str
    category: Optional[str] = None
    imagesUrls: Optional[List[str]] = Field(default_factory=list)
    originalClipUrls: Optional[List[str]] = Field(default_factory=list)
    mediaId: int = Field(default=2, description="Media ID for this source (e.g., 2 for digital eNews)")

# --- NEW MODEL for Flow 4 (/process/direct_images) ---
# This is what the crawler sends TO THE GATEWAY for direct image processing
class DirectImageProcessingPayload(BaseModel):
    imageDirectory: str # Path to the directory containing the images
    publicationName: str # Name of the publication
    editionName: Optional[str] = None # Name of the edition
    languageName: str # Language of the publication
    zoneName: str # Zone of the publication
    date: str # Date of the publication in DD-MM-YYYY format
    processingType: str = "direct_images" # Indicates this is direct image processing


# --- Endpoints ---
# Flow 1: /pipeline (Dashboard PDF Upload) - Stays the same
@app.post("/pipeline", response_model=TaskResponse, status_code=202)
async def enqueue_dashboard_pdf_processing(pdf: UploadFile = File(...), publicationName: str = Form(...), editionName: str = Form(None), languageName: str = Form(...), zoneName: str = Form(...), date: Optional[str] = Form(None), dpi: int = Form(200), quality: int = Form(85), resize_bool: bool = Form(True)):
    if not s3_client or not AWS_S3_BUCKET_NAME: raise HTTPException(status_code=503, detail="S3 not configured.")
    if not pdf.filename: raise HTTPException(status_code=400, detail="No file name.")
    safe_fn_base = "".join(c for c in os.path.splitext(pdf.filename)[0] if c.isalnum() or c in "._-")
    s3_key = f"incoming_dashboard_pdfs/{uuid.uuid4().hex}_{safe_fn_base}{os.path.splitext(pdf.filename)[1]}"
    try:
        await pdf.seek(0); s3_client.upload_fileobj(pdf.file, AWS_S3_BUCKET_NAME, s3_key, ExtraArgs={'ContentType': pdf.content_type or 'application/pdf'})
    except Exception as e: raise HTTPException(status_code=500, detail=f"S3 upload error: {e}")
    finally: await pdf.close()
    task_submission = celery_gateway_app.send_task("ocr_engine.process_document", args=[s3_key, publicationName, editionName, date, languageName, zoneName, dpi, quality, False, resize_bool]) # should_notify_node=False
    return TaskResponse(task_id=task_submission.id, message="PDF processing task queued (dashboard flow).")

# Flow 2: /crawl/newspaper_pdf (Crawler PDF Upload) - Stays the same
@app.post("/crawl/newspaper_pdf", response_model=SimpleAckResponse, status_code=202)
async def enqueue_crawler_pdf_processing(pdf: UploadFile = File(...), publicationName: str = Form(...), editionName: str = Form(None), languageName: str = Form(...), date: str = Form(...), zoneName: str = Form(...), dpi: int = Form(200), quality: int = Form(85), resize_bool: bool = Form(False)):
    if not s3_client or not AWS_S3_BUCKET_NAME: raise HTTPException(status_code=503, detail="S3 not configured.")
    if not pdf.filename: raise HTTPException(status_code=400, detail="No file name.")
    if not re.match(r'^\d{2}-\d{2}-\d{4}$', date): raise HTTPException(status_code=400, detail="Invalid date format.")
    safe_fn_base = "".join(c for c in os.path.splitext(pdf.filename)[0] if c.isalnum() or c in "._-")
    s3_key = f"incoming_crawler_pdfs/{uuid.uuid4().hex}_{safe_fn_base}{os.path.splitext(pdf.filename)[1]}"
    try:
        await pdf.seek(0); s3_client.upload_fileobj(pdf.file, AWS_S3_BUCKET_NAME, s3_key, ExtraArgs={'ContentType': pdf.content_type or 'application/pdf'})
    except Exception as e: raise HTTPException(status_code=500, detail=f"S3 upload error: {e}")
    finally: await pdf.close()
    task_submission = celery_gateway_app.send_task("ocr_engine.process_document", args=[s3_key, publicationName, editionName, date, languageName,zoneName, dpi, quality, True, resize_bool]) # should_notify_node=True
    return SimpleAckResponse(message="Crawler PDF processing task queued (will notify).", task_id=task_submission.id)

# Flow 3: /process/digital_s3_json (Digital Article from S3 JSON) - CORRECTED
@app.post("/process/digital_s3_json", response_model=SimpleAckResponse, status_code=202)
async def enqueue_s3_digital_article_processing(
    request_data: DigitalS3JsonPayloadFromCrawler = Body(...) # <<< USE CORRECTED INPUT MODEL
):
    if not request_data.s3_url or not request_data.s3_url.startswith("s3://"):
        raise HTTPException(status_code=400, detail="Valid s3_url starting with s3:// is required.")

    # This dictionary IS the S3DigitalArticleAnalysisTaskInput for the Celery task
    task_payload_for_celery = {
        "s3_json_url": request_data.s3_url,
        "request_media_id": request_data.mediaId, # mediaId from crawler
        "request_site_name": request_data.site_name, # Fallback site_name
        "request_timestamp": request_data.timestamp # Crawler's event timestamp
    }
    
    task_submission = celery_gateway_app.send_task(
        "ocr_engine.analyze_s3_digital_article_json",
        args=[task_payload_for_celery],
    )
    return SimpleAckResponse(message="Digital article from S3 JSON queued for analysis.", task_id=task_submission.id)

# --- Task Status Endpoint ---
@app.get("/tasks/{task_id}", response_model=StatusResponse)
async def get_task_status(task_id: str):
    async_result = celery_gateway_app.AsyncResult(task_id)
    response_data = {"state": async_result.state, "info": async_result.info}
    if async_result.successful(): response_data["result"] = async_result.get()
    elif async_result.failed(): response_data["result"] = str(async_result.result)
    return StatusResponse(**response_data)

# Flow 4: /process/direct_images (Direct Image Processing from Crawler)
@app.post("/process/direct_images", response_model=SimpleAckResponse, status_code=202)
async def process_direct_images(request_data: DirectImageProcessingPayload = Body(...)):
    """
    Process images directly from a directory without S3 upload/download.
    This endpoint is used by the crawler to notify the gateway about images ready for processing.
    """
    # Validate the image directory
    if not os.path.exists(request_data.imageDirectory):
        raise HTTPException(status_code=400, detail=f"Image directory not found: {request_data.imageDirectory}")
    
    # Check if there are images in the directory
    image_files = [f for f in os.listdir(request_data.imageDirectory) 
                  if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    
    if not image_files:
        raise HTTPException(status_code=400, detail=f"No image files found in directory: {request_data.imageDirectory}")
    
    # Send task to OCR engine
    task_submission = celery_gateway_app.send_task(
        "ocr_engine.process_direct_images",
        args=[
            request_data.imageDirectory,
            request_data.publicationName,
            request_data.editionName,
            request_data.date,
            request_data.languageName,
            request_data.zoneName,
            True  # should_notify_node=True
        ]
    )
    
    return SimpleAckResponse(
        message=f"Direct image processing task queued for {len(image_files)} images in {request_data.imageDirectory}",
        task_id=task_submission.id
    )

# Flow 5: /process/digital_raw_json (Digital Article from Raw JSON)
@app.post("/process/digital_raw_json", response_model=SimpleAckResponse, status_code=202)
async def process_digital_raw_json(request_data: DigitalRawJsonPayload = Body(...)):
    """
    Process digital article directly from raw JSON data without S3 upload/download.
    This endpoint is used by the digital crawler to send article data directly to the gateway.
    """
    # Validate the content
    if not request_data.content or len(request_data.content.strip()) < 50:
        raise HTTPException(status_code=400, detail="Article content is too short or empty")
    
    # Prepare the payload for the OCR engine
    task_payload = request_data.model_dump(exclude_none=True)
    
    # Send task to OCR engine
    task_submission = celery_gateway_app.send_task(
        "ocr_engine.analyze_digital_raw_json",
        args=[task_payload],
    )
    
    return SimpleAckResponse(
        message=f"Digital article processing task queued for {request_data.title or 'Untitled Article'}",
        task_id=task_submission.id
    )

@app.get("/")
async def root(): return {"message": "Multi-Source Content Processing Gateway API"}