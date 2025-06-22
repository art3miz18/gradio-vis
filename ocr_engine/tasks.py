# --- Final tasks.py (Flow 2 & 3 Cleaned and Validated) ---
import os
import tempfile
import shutil
import time
from typing import Optional, Dict, Any, List
import httpx
import json
import uuid
import asyncio
import datetime
import re

from celery_app import celery_ocr_engine_app
import pipeline_logic
from services.content_analyzer import analyze_news_article_content, analyze_digital_text_content
from services.s3_handler import upload_file_to_s3, save_analysis_json_and_upload
from models import (
    NodeJsPayload,
    NodeJsArticleDetailInPayload,
    S3DigitalArticleAnalysisTaskInput,
    DigitalArticleS3JsonContent,
    PDFProcessingResponse
)
from config import s3_client as task_s3_client, AWS_S3_BUCKET_NAME_CONFIG as TASK_S3_BUCKET_NAME, AWS_REGION_CONFIG
import config

NODE_APP_CALLBACK_URL = os.getenv("CRAWLER_API_URL")
if not NODE_APP_CALLBACK_URL:
    print("\u26a0\ufe0f WARNING: CRAWLER_API_URL not set.")

@celery_ocr_engine_app.task(name="ocr_engine.notify_node_on_completion", bind=True, max_retries=5, default_retry_delay=10*60, acks_late=True)
def notify_node_on_completion_task(self, notification_data_wrapper: Dict[str, Any], target_url: str):
    task_id = self.request.id
    pid = os.getpid()
    actual_payload = notification_data_wrapper.get("processed_data_payload_for_node")
    orig_task_id = notification_data_wrapper.get("celery_task_id_that_generated_this", "N/A")
    log_prefix = f"NotificationTask[{task_id}/{pid}] for Original[{orig_task_id}]"

    if not target_url or not isinstance(actual_payload, dict):
        return {"status": "skipped_or_invalid_payload"}

    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(target_url, json=actual_payload)
            response.raise_for_status()
        print(f"{log_prefix}: Notify SUCCESS. Status: {response.status_code}")
        return {"status": "notified_successfully", "response_code": response.status_code}
    except Exception as e:
        err_text = str(e)
        if isinstance(e, httpx.HTTPStatusError) and hasattr(e, 'response'):
            err_text = e.response.text[:200]
        print(f"{log_prefix}: Notify FAILED: {type(e).__name__} - {err_text}. Retrying...")
        raise self.retry(exc=e, countdown=int(self.default_retry_delay * (1.5**self.request.retries)))

@celery_ocr_engine_app.task(name="ocr_engine.process_document", bind=True, acks_late=True, max_retries=3, default_retry_delay=5*60)
def process_document_task(self, s3_pdf_key: str, publication_name: str, edition_name: Optional[str], document_date: Optional[str], language_name: str, zoneName: Optional[str], dpi: int, quality: int, should_notify_node: bool, resize_bool: bool):
    task_id = self.request.id
    pid = os.getpid()
    downloaded_pdf_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=f"celery_dl_{task_id.replace('-', '')}_") as tmp_f:
            downloaded_pdf_path = tmp_f.name
        task_s3_client.download_file(TASK_S3_BUCKET_NAME, s3_pdf_key, downloaded_pdf_path)

        pdf_output = pipeline_logic.process_newspaper_pdf_sync_caller(
            downloaded_pdf_path, publication_name, edition_name, document_date, language_name, zoneName, dpi, quality, resize_bool
        )

        if should_notify_node and NODE_APP_CALLBACK_URL:
            valid_articles = []
            for art in pdf_output.get("articles", []):
                if isinstance(art, dict) and not art.get("error"):
                    valid_articles.append({
                        "unique_article_id": art.get("unique_article_id"),
                        "pagenumber": art.get("pagenumber"),
                        "language": art.get("language"),
                        "heading": art.get("english_heading") or art.get("heading"),
                        "content": art.get("content"),
                        "english_heading": art.get("english_heading"),
                        "english_content": art.get("english_content"),
                        "english_summary":art.get("english_summary"),
                        "sentiment": art.get("sentiment"),
                        "ministryName": art.get("ministryName", "Unknown"),
                        "AdditionMinisrtyName": art.get("AdditionMinisrtyName", []),
                        "extracted_date_from_gemini": "unknown",
                        "path": f"s3://{TASK_S3_BUCKET_NAME}/{s3_pdf_key}",
                        "image_url": art.get("image_url")
                    })

            if valid_articles:
                payload = NodeJsPayload(
                    mediaId=1,
                    publication=pdf_output.get("publication", publication_name),
                    edition=pdf_output.get("edition", edition_name) or "",
                    language=pdf_output.get("language", language_name),
                    zoneName=pdf_output.get("zoneName", zoneName),
                    date=pdf_output.get("date", document_date or datetime.date.today().strftime("%d-%m-%Y")),
                    articles=valid_articles
                )
                print("\n--- DEBUG: FINAL PAYLOAD FLOW 2 (PDF) ---")
                print(json.dumps(payload.model_dump(exclude_none=True), indent=2))

                notify_node_on_completion_task.delay({
                    "celery_task_id_that_generated_this": task_id,
                    "processed_data_payload_for_node": payload.model_dump(exclude_none=True)
                }, NODE_APP_CALLBACK_URL)

        return pdf_output
    except Exception as e:
        print(f"PDFTask[{task_id}/{pid}]: ERROR: {e}")
        import traceback; traceback.print_exc()
        raise self.retry(exc=e)
    finally:
        if downloaded_pdf_path and os.path.exists(downloaded_pdf_path):
            os.remove(downloaded_pdf_path)

@celery_ocr_engine_app.task(name="ocr_engine.process_direct_images", bind=True, acks_late=True, max_retries=3, default_retry_delay=5*60)
def process_direct_images_task(self, image_directory: str, publication_name: str, edition_name: Optional[str], document_date: str, language_name: str, zone_name: Optional[str], should_notify_node: bool):
    """
    Process images directly from a directory without S3 upload/download.
    This task is used by the gateway to process images from the crawler.
    """
    task_id = self.request.id
    pid = os.getpid()
    log_prefix = f"DirectImagesTask[{task_id}/{pid}]"
    
    try:
        # Validate the image directory
        if not os.path.exists(image_directory):
            print(f"{log_prefix}: Image directory not found: {image_directory}")
            return {"error": f"Image directory not found: {image_directory}"}
        
        # Get list of image files
        image_files = sorted([
            os.path.join(image_directory, f) for f in os.listdir(image_directory)
            if f.lower().endswith(('.png', '.jpg', '.jpeg'))
        ])
        
        if not image_files:
            print(f"{log_prefix}: No image files found in directory: {image_directory}")
            return {"error": f"No image files found in directory: {image_directory}"}
        
        print(f"{log_prefix}: Processing {len(image_files)} images from {image_directory}")
        
        # Create a task-specific temp directory
        task_specific_temp_dir = tempfile.mkdtemp(prefix=f"ocr_pipeline_direct_img_task_{uuid.uuid4().hex[:6]}_")
        print(f"{log_prefix}: Created temp dir {task_specific_temp_dir}")
        
        try:
            # Set up the event loop
            try:
                loop = asyncio.get_event_loop_policy().get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:  # No current event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Process all images in parallel using the same approach as PDF processing
            # First, prepare the page processing coroutines
            page_processing_coroutines = []
            page_numbers = []
            
            for idx, image_path in enumerate(image_files):
                # Extract page number from filename
                filename = os.path.basename(image_path)
                page_match = re.search(r'page_(\d+)', filename)
                page_number = int(page_match.group(1)) if page_match else idx + 1
                page_numbers.append(page_number)
                
                # Generate a unique file prefix for IDs
                file_prefix = f"{publication_name.replace(' ', '_')}_{document_date.replace('/', '-').replace(' ', '_')}_{page_number}"
                
                # Add the coroutine to the list
                page_processing_coroutines.append(
                    pipeline_logic._process_single_page_segment_and_crop(
                        full_page_image_path=image_path,
                        page_number=page_number,
                        file_prefix_for_ids=file_prefix,
                        page_processing_base_temp_dir=task_specific_temp_dir,
                        dpi_for_arcanum=pipeline_logic.DEFAULT_DPI,
                        crop_jpeg_quality=pipeline_logic.DEFAULT_JPEG_QUALITY
                    )
                )
            
            # Process all pages in parallel
            all_article_crop_infos = []
            if page_processing_coroutines:
                print(f"{log_prefix}: Processing {len(page_processing_coroutines)} pages in parallel")
                time_s = time.monotonic()
                results_from_page_processing = loop.run_until_complete(asyncio.gather(*page_processing_coroutines, return_exceptions=True))
                print(f"{log_prefix}: Page processing took {time.monotonic() - time_s:.2f}s")
                
                for i, page_result_or_exc in enumerate(results_from_page_processing):
                    page_num = page_numbers[i] if i < len(page_numbers) else i + 1
                    if isinstance(page_result_or_exc, list):
                        print(f"{log_prefix}: Page {page_num} processing found {len(page_result_or_exc)} article crops")
                        all_article_crop_infos.extend(page_result_or_exc)
                    elif isinstance(page_result_or_exc, Exception):
                        print(f"{log_prefix}: Page {page_num} processing FAILED: {page_result_or_exc}")
                    else:
                        print(f"{log_prefix}: Page {page_num} processing returned unexpected type: {type(page_result_or_exc)}")
            
            # Process all article crops in parallel
            analysis_coroutines = []
            if all_article_crop_infos:
                print(f"{log_prefix}: Processing {len(all_article_crop_infos)} article crops in parallel")
                time_s = time.monotonic()
                for article_meta in all_article_crop_infos:
                    analysis_coroutines.append(analyze_news_article_content(article_meta, language_name))
                raw_analysis_results = loop.run_until_complete(asyncio.gather(*analysis_coroutines, return_exceptions=True))
                print(f"{log_prefix}: Article analysis took {time.monotonic() - time_s:.2f}s")
            else:
                raw_analysis_results = []
                print(f"{log_prefix}: No articles to analyze after segmentation")
            
            # Process the analysis results
            articles = []
            original_article_crops_map = {art_info.get("unique_article_id",""): art_info for art_info in all_article_crop_infos if art_info.get("unique_article_id")}
            
            for i, result_or_exc in enumerate(raw_analysis_results):
                try:
                    original_id = all_article_crop_infos[i].get("unique_article_id", f"unknown_at_idx_{i}") if i < len(all_article_crop_infos) else f"unknown_raw_res_{i}"
                    
                    if isinstance(result_or_exc, Exception):
                        print(f"{log_prefix}: Analysis exception for article {original_id}: {result_or_exc}")
                        continue
                    
                    if result_or_exc is None:
                        print(f"{log_prefix}: Article {original_id} classified as advertisement. Skipping.")
                        continue
                    
                    if result_or_exc.get("error"):
                        print(f"{log_prefix}: Analysis error for article {original_id}: {result_or_exc.get('error')}")
                        continue
                    
                    # Skip articles with unknown ministry
                    if result_or_exc.get("ministryName", "Unknown") == "Unknown":
                        print(f"{log_prefix}: Article {original_id} has unknown ministry. Skipping.")
                        continue
                    
                    # Add page number if missing
                    if 'pagenumber' not in result_or_exc:
                        original_detail = original_article_crops_map.get(original_id)
                        if original_detail:
                            result_or_exc['pagenumber'] = original_detail.get('pagenumber', 0)
                    
                    # Add the article to the list
                    articles.append(result_or_exc)
                except Exception as e:
                    print(f"{log_prefix}: Error processing analysis result at index {i}: {e}")
            
            # Upload all article images to S3 in parallel
            if articles and config.s3_client and config.AWS_S3_BUCKET_NAME_CONFIG:
                print(f"{log_prefix}: Uploading {len(articles)} article images to S3 in parallel")
                time_s = time.monotonic()
                
                # Format date as YYYY-MM-DD if it's not already
                formatted_date = document_date
                try:
                    if document_date and len(document_date.split('-')) == 3:
                        # Date is already in YYYY-MM-DD format
                        pass
                    elif document_date and len(document_date.split('/')) == 3:
                        # Convert DD/MM/YYYY to YYYY-MM-DD
                        day, month, year = document_date.split('/')
                        formatted_date = f"{year}-{month}-{day}"
                    elif document_date:
                        # Try to handle other date formats
                        for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y.%m.%d"]:
                            try:
                                dt = datetime.datetime.strptime(document_date, fmt)
                                formatted_date = dt.strftime("%Y-%m-%d")
                                break
                            except ValueError:
                                continue
                except Exception as e:
                    print(f"{log_prefix}: Error formatting date '{document_date}': {e}. Using original value.")
                
                # Prepare upload coroutines for both image and JSON
                image_upload_coroutines = []
                json_upload_coroutines = []
                
                # Create a subdirectory for JSON files
                json_output_dir = os.path.join(task_specific_temp_dir, "analysis_json_outputs")
                os.makedirs(json_output_dir, exist_ok=True)
                
                for i, article in enumerate(articles):
                    if 'path' in article and os.path.exists(article['path']):
                        # Image upload coroutine
                        image_upload_coroutines.append((
                            i,
                            upload_file_to_s3(
                                file_path=article['path'],
                                publication_name=publication_name,
                                edition_name=edition_name if edition_name else "default_edition",
                                date_str=formatted_date,
                                page_number=article.get('pagenumber', 1),
                                object_name_override=f"{article['unique_article_id']}.jpg"
                            )
                        ))
                        
                        # JSON upload coroutine
                        json_upload_coroutines.append((
                            i,
                            save_analysis_json_and_upload(
                                analysis_data=article,
                                publication_name=publication_name,
                                edition_name=edition_name if edition_name else "default_edition",
                                date_str=formatted_date,
                                page_number=article.get('pagenumber', 1),
                                task_temp_dir=json_output_dir
                            )
                        ))
                
                # Process image uploads
                if image_upload_coroutines:
                    print(f"{log_prefix}: Starting {len(image_upload_coroutines)} parallel image uploads")
                    image_s3_results = loop.run_until_complete(asyncio.gather(*[task for _, task in image_upload_coroutines], return_exceptions=True))
                    
                    # Process image upload results
                    for (article_index, _), s3_result in zip(image_upload_coroutines, image_s3_results):
                        if isinstance(s3_result, Exception):
                            print(f"{log_prefix}: Image upload error for article {articles[article_index]['unique_article_id']}: {s3_result}")
                            articles[article_index]["image_url"] = "UPLOAD_FAILED"
                        else:
                            articles[article_index]["image_url"] = s3_result
                            print(f"{log_prefix}: Set image_url for article {articles[article_index]['unique_article_id']}: {s3_result}")
                
                # Process JSON uploads
                if json_upload_coroutines:
                    print(f"{log_prefix}: Starting {len(json_upload_coroutines)} parallel JSON uploads")
                    json_s3_results = loop.run_until_complete(asyncio.gather(*[task for _, task in json_upload_coroutines], return_exceptions=True))
                    
                    # Process JSON upload results
                    for (article_index, _), s3_result in zip(json_upload_coroutines, json_s3_results):
                        if isinstance(s3_result, Exception):
                            print(f"{log_prefix}: JSON upload error for article {articles[article_index]['unique_article_id']}: {s3_result}")
                            articles[article_index]["ocr_output_url"] = "UPLOAD_FAILED"
                        else:
                            articles[article_index]["ocr_output_url"] = s3_result
                            print(f"{log_prefix}: Set ocr_output_url for article {articles[article_index]['unique_article_id']}: {s3_result}")
                
                print(f"{log_prefix}: S3 uploads took {time.monotonic() - time_s:.2f}s")
        except Exception as e:
            print(f"{log_prefix}: Error in parallel processing: {e}")
            import traceback; traceback.print_exc()
        finally:
            # Clean up the temp directory
            if task_specific_temp_dir and os.path.exists(task_specific_temp_dir):
                try:
                    shutil.rmtree(task_specific_temp_dir)
                    print(f"{log_prefix}: Cleaned temp dir {task_specific_temp_dir}")
                except Exception as e:
                    print(f"{log_prefix}: Error cleaning temp dir {task_specific_temp_dir}: {e}")
        
        # Prepare the output
        pdf_output = {
            "publication": publication_name,
            "edition": edition_name,
            "date": document_date,
            "language": language_name,
            "zoneName": zone_name,
            "articles": articles,
            "total_articles": len(articles),
            "processing_type": "direct_images"
        }
        
        # Notify Node.js if required
        if should_notify_node and NODE_APP_CALLBACK_URL:
            valid_articles = []
            for art in articles:
                if isinstance(art, dict) and not art.get("error"):
                    valid_articles.append({
                        "unique_article_id": art.get("unique_article_id"),
                        "pagenumber": art.get("pagenumber"),
                        "language": art.get("language"),
                        "heading": art.get("english_heading") or art.get("heading"),
                        "content": art.get("content"),
                        "english_heading": art.get("english_heading"),
                        "english_content": art.get("english_content"),
                        "english_summary": art.get("english_summary"),
                        "sentiment": art.get("sentiment"),
                        "ministryName": art.get("ministryName", "Unknown"),
                        "AdditionMinisrtyName": art.get("AdditionMinisrtyName", []),
                        "extracted_date_from_gemini": "unknown",
                        "path": image_directory,
                        "image_url": art.get("image_url")
                    })
            
            if valid_articles:
                payload = NodeJsPayload(
                    mediaId=1,
                    publication=publication_name,
                    edition=edition_name or "",
                    language=language_name,
                    zoneName=zone_name,
                    date=document_date,
                    articles=valid_articles
                )
                print(f"\n--- DEBUG: FINAL PAYLOAD FLOW 4 (DIRECT IMAGES) ---")
                print(json.dumps(payload.model_dump(exclude_none=True), indent=2))
                
                notify_node_on_completion_task.delay({
                    "celery_task_id_that_generated_this": task_id,
                    "processed_data_payload_for_node": payload.model_dump(exclude_none=True)
                }, NODE_APP_CALLBACK_URL)
        
        return pdf_output
    
    except Exception as e:
        print(f"{log_prefix}: ERROR: {e}")
        import traceback; traceback.print_exc()
        raise self.retry(exc=e)

@celery_ocr_engine_app.task(name="ocr_engine.analyze_s3_digital_article_json", bind=True, acks_late=True, max_retries=3, default_retry_delay=3*60)
def analyze_s3_digital_article_json_task(self, task_input_payload_dict: Dict[str, Any]):
    current_task_id = self.request.id
    pid = os.getpid()
    try:
        task_input = S3DigitalArticleAnalysisTaskInput(**task_input_payload_dict)
        log_prefix = f"DigitalS3Task[{current_task_id}/{pid}]"

        async def run_analysis():
            s3_parts = task_input.s3_json_url.replace("s3://", "").split("/", 1)
            s3_bucket, s3_key = s3_parts[0], s3_parts[1]
            s3_obj = await asyncio.to_thread(task_s3_client.get_object, Bucket=s3_bucket, Key=s3_key)
            json_str = await asyncio.to_thread(s3_obj['Body'].read().decode, 'utf-8')
            s3_json = DigitalArticleS3JsonContent(**json.loads(json_str))

            result = await analyze_digital_text_content(
                text_content=s3_json.content,
                original_language=s3_json.language,
                original_heading=s3_json.title
            )
            
            if result.get("error") == "advertisement_filtered":
                print(f"{log_prefix}: Skipping advertisement content.")
                return None\
                
            # ——— FIXED MINISTRY VALIDATION ———
            ministry = result.get("ministryName", "")
            if not ministry or ministry.lower() == "unknown":
                print(f"{log_prefix}: Ministry name is missing or Unknown.")
                return {"error": "Ministry name is 'Unknown' or missing"}
            
            article = NodeJsArticleDetailInPayload(
                heading=result.get("english_heading") or result.get("heading") or s3_json.title,
                content=s3_json.content,
                english_heading=result.get("english_heading"),
                english_content=result.get("english_content"),
                english_summary=result.get("english_summary"),
                sentiment=result.get("sentiment"),
                ministryName=result.get("ministryName", "Unknown"),
                AdditionMinisrtyName=result.get("AdditionMinisrtyName", []),
                image_url=s3_json.imagesUrls[0] if s3_json.imagesUrls else None,
                category=s3_json.category,
                language=result.get("language", s3_json.language),
                authors=s3_json.authors or [],
                originalClipUrls=[s3_json.url],
                pagenumber=None
            )

            payload = NodeJsPayload(
                mediaId=2,
                publication=s3_json.source or task_input.request_site_name or "N/A",
                edition="",
                zoneName="Central",
                language=article.language or "N/A",
                date=result.get("date") or s3_json.date_published or task_input.request_timestamp or datetime.date.today().strftime("%d-%m-%Y"),
                articles=[article.model_dump(exclude_none=True)]
            )
            print("\n--- DEBUG: FINAL PAYLOAD FLOW 3 (DIGITAL) ---")
            print(json.dumps(payload.model_dump(exclude_none=True), indent=2))
            return payload.model_dump(exclude_none=True)

        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        result_payload = loop.run_until_complete(run_analysis())

        if result_payload and not result_payload.get("task_error"):
            notify_node_on_completion_task.delay({
                "celery_task_id_that_generated_this": current_task_id,
                "processed_data_payload_for_node": result_payload
            }, NODE_APP_CALLBACK_URL)
            return result_payload

        return {"task_error": "No result or error in result."}

    except Exception as e:
        print(f"{log_prefix}: OUTER ERROR: {e}")
        import traceback; traceback.print_exc()
        raise self.retry(exc=e)

@celery_ocr_engine_app.task(name="ocr_engine.analyze_digital_raw_json", bind=True, acks_late=True, max_retries=3, default_retry_delay=3*60)
def analyze_digital_raw_json_task(self, raw_json_payload: Dict[str, Any]):
    current_task_id = self.request.id
    pid = os.getpid()
    try:
        # Convert the raw JSON payload to a DigitalArticleS3JsonContent object
        article_content = DigitalArticleS3JsonContent(**raw_json_payload)
        log_prefix = f"DigitalRawTask[{current_task_id}/{pid}]"
        
        print(f"{log_prefix}: Processing article: {article_content.title}")

        async def run_analysis():
            result = await analyze_digital_text_content(
                text_content=article_content.content,
                original_language=article_content.language,
                original_heading=article_content.title
            )
            
            if result.get("error") == "advertisement_filtered":
                print(f"{log_prefix}: Skipping advertisement content.")
                return None
                
            # ——— FIXED MINISTRY VALIDATION ———
            ministry = result.get("ministryName", "")
            if not ministry or ministry.lower() == "unknown":
                print(f"{log_prefix}: Ministry name is missing or Unknown.")
                return {"error": "Ministry name is 'Unknown' or missing"}
            
            article = NodeJsArticleDetailInPayload(
                heading=result.get("english_heading") or result.get("heading") or article_content.title,
                content=article_content.content,
                english_heading=result.get("english_heading"),
                english_content=result.get("english_content"),
                english_summary=result.get("english_summary"),
                sentiment=result.get("sentiment"),
                ministryName=result.get("ministryName", "Unknown"),
                AdditionMinisrtyName=result.get("AdditionMinisrtyName", []),
                image_url=article_content.imagesUrls[0] if article_content.imagesUrls else None,
                category=article_content.category,
                language=result.get("language", article_content.language),
                authors=article_content.authors or [],
                originalClipUrls=[article_content.url] if article_content.url else [],
                pagenumber=None
            )

            # Get the media ID from the raw JSON payload or use default value 2
            media_id = raw_json_payload.get("mediaId", 2)

            payload = NodeJsPayload(
                mediaId=media_id,
                publication=article_content.source or "N/A",
                edition="",
                zoneName="Central",
                language=article.language or "N/A",
                date=result.get("date") or article_content.date_published or datetime.date.today().strftime("%d-%m-%Y"),
                articles=[article.model_dump(exclude_none=True)]
            )
            print("\n--- DEBUG: FINAL PAYLOAD FLOW 5 (DIGITAL RAW) ---")
            print(json.dumps(payload.model_dump(exclude_none=True), indent=2))
            return payload.model_dump(exclude_none=True)

        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        result_payload = loop.run_until_complete(run_analysis())

        if result_payload and not result_payload.get("task_error"):
            notify_node_on_completion_task.delay({
                "celery_task_id_that_generated_this": current_task_id,
                "processed_data_payload_for_node": result_payload
            }, NODE_APP_CALLBACK_URL)
            return result_payload

        return {"task_error": "No result or error in result."}

    except Exception as e:
        print(f"{log_prefix}: OUTER ERROR: {e}")
        import traceback; traceback.print_exc()
        raise self.retry(exc=e)