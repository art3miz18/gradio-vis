# ocr_engine/pipeline_logic.py
import os
import time
import asyncio
import uuid
import shutil
import tempfile
import re
import datetime
from typing import Optional, List, Dict, Any
from PIL import Image

# Absolute imports from the /app root in the Docker container
import config
from models import PDFProcessingResponse
from services.pdf_converter import convert_pdf_to_images_with_mutool
from services.image_processor import crop_articles_from_segmentation_data
from services.content_analyzer import analyze_news_article_content # Async
from services.s3_handler import upload_file_to_s3, save_analysis_json_and_upload # Async
from util.dummyFile import DummyUpload # For the sync_caller

# Import Arcanum client directly here as it's part of page processing
from newspaper_segmentation_client import run_newspaper_segmentation

# Constants for direct image processing
DEFAULT_DPI = 200
DEFAULT_JPEG_QUALITY = 85

# Helper for parallel page processing: Segmentation + Cropping (including Arcanum ad filter)
async def _process_single_page_segment_and_crop(
    full_page_image_path: str,
    page_number: int,
    file_prefix_for_ids: str, # For unique_article_id generation
    page_processing_base_temp_dir: str, # e.g., task_temp_dir/page_processing_outputs
    dpi_for_arcanum: int, # DPI that was used to generate the full_page_image_path
    crop_jpeg_quality: int
) -> List[Dict[str, Any]]: # Returns list of article crop info dicts for this page
    pid = os.getpid()
    # Create a specific subdirectory for this page's intermediate files and final crops
    current_page_output_dir = os.path.join(page_processing_base_temp_dir, f"page_{page_number}")
    os.makedirs(current_page_output_dir, exist_ok=True)
    
    article_crops_final_dir = os.path.join(current_page_output_dir, "article_crops") # Where final crops go
    # crop_articles_from_segmentation_data will create this if it doesn't exist.

    # print(f"[{pid}] PageProcessor Page {page_number}: Starting for {os.path.basename(full_page_image_path)}")
    
    try:
        original_page_pil = Image.open(full_page_image_path)
        
        # Resize large images before processing
        max_dimension = 3000  # Maximum width or height
        if original_page_pil.width > max_dimension or original_page_pil.height > max_dimension:
            print(f"[{pid}] PageProcessor Page {page_number}: Resizing large image from {original_page_pil.width}x{original_page_pil.height}")
            # Create a temporary file for the resized image
            resized_image_path = os.path.join(current_page_output_dir, f"resized_{os.path.basename(full_page_image_path)}")
            # Use thumbnail to maintain aspect ratio
            original_page_pil.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            original_page_pil.save(resized_image_path, quality=90)
            print(f"[{pid}] PageProcessor Page {page_number}: Resized to {original_page_pil.width}x{original_page_pil.height}")
            # Use the resized image for segmentation
            segmentation_image_path = resized_image_path
        else:
            segmentation_image_path = full_page_image_path

        def segment_sync_with_arcanum(): # Arcanum call runs in a thread
            with open(segmentation_image_path, "rb") as img_file_obj:
                return run_newspaper_segmentation(
                    img_file_obj,
                    api_key=config.SEGMENTATION_API_KEY,
                    dpi=dpi_for_arcanum # Inform Arcanum about the image's DPI
                )

        arcanum_response = await asyncio.to_thread(segment_sync_with_arcanum)
        
        if not arcanum_response or not isinstance(arcanum_response.get("articles"), list):
            msg = arcanum_response.get("message", "Invalid or empty response") if isinstance(arcanum_response, dict) else "Unknown Arcanum error"
            print(f"[{pid}] PageProcessor Page {page_number}: Arcanum segmentation failed or no articles. Message: {msg}")
            return []

        # print(f"[{pid}] PageProcessor Page {page_number}: Segmentation complete. Cropping articles...")
        article_crop_infos_on_page = crop_articles_from_segmentation_data(
            original_page_pil_image=original_page_pil,
            arcanum_segmentation_response=arcanum_response,
            page_number=page_number,
            file_prefix_for_ids=file_prefix_for_ids,
            article_crops_output_dir=article_crops_final_dir, # Save crops here
            crop_jpeg_quality=crop_jpeg_quality
        )
        print(f"[{pid}] PageProcessor Page {page_number}: Finished. Found {len(article_crop_infos_on_page)} non-ad articles.")
        return article_crop_infos_on_page
    except Exception as e:
        print(f"[{pid}] PageProcessor Page {page_number}: ERROR - {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return []


# Main async orchestrator
async def _orchestrate_pdf_processing(
    local_pdf_path: str,
    publication_name_param: str,
    edition_name_param: str,
    date_param: Optional[str],
    language_name_param: str,
    zone_name_param: Optional[str],
    dpi_param: int,
    quality_param: int, # For full page images from pdf2image
    task_temp_dir: str,
    resize_bool: bool    
) -> Dict[str, Any]:
    start_time = time.monotonic() # Use monotonic for duration
    request_id = uuid.uuid4().hex[:8]
    process_pid = os.getpid()
    log_prefix = f"[{request_id}/{process_pid}]"
    print(f"{log_prefix} Orchestrating PDF: {os.path.basename(local_pdf_path)}, DPI: {dpi_param}, PageImgQuality: {quality_param}")

    all_s3_file_urls_for_response = []
    final_response_articles_list = []

    try:
        # --- Step 1: Convert PDF to full-page images ---
        time_s = time.monotonic()
        initial_full_page_image_paths = convert_pdf_to_images_with_mutool(
                                                            local_pdf_path,
                                                            task_temp_dir,
                                                            dpi=dpi_param,
                                                            jpeg_quality=quality_param,
                                                            use_resize = resize_bool,
                                                            max_dimension=3000,
                                                            chunk_size=3,  
                                                            mutool_timeout=90, 
                                                            max_retries=3,  
                                                            memory_limit_mb=1500)   
        total_pages = len(initial_full_page_image_paths)
        print(f"{log_prefix} PDF Conversion took: {time.monotonic() - time_s:.2f}s. Pages: {total_pages}")
        if not total_pages:
            return PDFProcessingResponse(publication=publication_name_param, edition=edition_name_param, date=date_param or "unknown", language=language_name_param, zoneName=zone_name_param,  total_pages=0, articles=[{"error": ""}], file_urls="").model_dump()


        # --- Step 2: Parallel Page Processing (Arcanum Segmentation + Cropping with Arcanum Ad Filter) ---
        time_s = time.monotonic()
        page_processing_coroutines = []
        file_id_prefix = os.path.splitext(os.path.basename(local_pdf_path))[0] + f"_{request_id}"
        page_processing_base_temp = os.path.join(task_temp_dir, "page_processing_outputs")
        # os.makedirs(page_processing_base_temp, exist_ok=True) # _process_single_page_segment_and_crop handles its own subdir creation

        # Define a quality for the article crops. Could be same as page quality or different.
        article_crop_jpeg_quality = 85 # Example: can be a new parameter if needed

        for i, page_image_file_path in enumerate(initial_full_page_image_paths):
            page_num = i + 1
            page_processing_coroutines.append(
                _process_single_page_segment_and_crop( # Ensure this function exists or is correctly defined
                    full_page_image_path=page_image_file_path,
                    page_number=page_num,
                    file_prefix_for_ids=file_id_prefix,
                    page_processing_base_temp_dir=page_processing_base_temp,
                    dpi_for_arcanum=dpi_param,
                    crop_jpeg_quality=article_crop_jpeg_quality # <<< PASSING THE CROP QUALITY
                )
            )
        
        all_article_crop_infos = []
        if page_processing_coroutines:
            results_from_page_processing = await asyncio.gather(*page_processing_coroutines, return_exceptions=True)
            for i, page_result_or_exc in enumerate(results_from_page_processing): # Iterate with index
                page_num_for_log = i + 1 # Use index to approximate page number for logging if order is maintained
                if isinstance(page_result_or_exc, list): 
                    all_article_crop_infos.extend(page_result_or_exc)
                elif isinstance(page_result_or_exc, Exception): 
                    print(f"{log_prefix} Page {page_num_for_log} segmentation/cropping FAILED: {page_result_or_exc}")
                else:
                    print(f"{log_prefix} Page {page_num_for_log} processing returned unexpected type: {type(page_result_or_exc)}")

        print(f"{log_prefix} Page Segmentation/Cropping took: {time.monotonic() - time_s:.2f}s. Found {len(all_article_crop_infos)} potential article crops.")

        # ... (Rest of the function: Step 3, 4, 5, 6 and return statement remain the same as in the previous "full file output") ...

        # --- Step 3: Parallel Article Analysis (Secondary Gemini Ad Check + Content Analysis) ---
        analysis_coroutines = []
        if all_article_crop_infos:
            time_s = time.monotonic()
            for article_meta in all_article_crop_infos: # These have passed Arcanum's ad filter
                analysis_coroutines.append(analyze_news_article_content(article_meta, language_name_param))
            raw_analysis_results = await asyncio.gather(*analysis_coroutines, return_exceptions=True)
            print(f"{log_prefix} Gemini Analysis (all articles) took: {time.monotonic() - time_s:.2f}s.")
        else:
            raw_analysis_results = []
            print(f"{log_prefix} No articles to analyze after Arcanum filtering.")


        # --- Step 4: Process Gemini analysis results & Filter (Unknown Ministry, Errors) ---
        successfully_analyzed_metadata_for_upload = []
        analysis_error_metadata_for_response = []
        all_extracted_dates_from_gemini = []
        # Ensure all_article_crop_infos is populated before this line if used for map
        original_article_crops_map = {art_info.get("unique_article_id",""): art_info for art_info in all_article_crop_infos if art_info.get("unique_article_id")}


        for i, result_or_exc in enumerate(raw_analysis_results):
            original_id = all_article_crop_infos[i].get("unique_article_id", f"unknown_at_idx_{i}") if i < len(all_article_crop_infos) else f"unknown_raw_res_{i}"
            if isinstance(result_or_exc, Exception):
                analysis_error_metadata_for_response.append({"unique_article_id": original_id, "error": f"Analysis exception: {str(result_or_exc)}"})
                continue
            if result_or_exc is None: continue 
            if result_or_exc.get("error"):
                analysis_error_metadata_for_response.append(result_or_exc)
                continue
            
            current_unique_id_from_analysis = result_or_exc.get('unique_article_id', original_id)
            
            if 'path' not in result_or_exc: 
                original_detail = original_article_crops_map.get(current_unique_id_from_analysis)
                if original_detail: result_or_exc['path'] = original_detail.get('path')
            if 'pagenumber' not in result_or_exc: 
                original_detail = original_article_crops_map.get(current_unique_id_from_analysis)
                if original_detail: result_or_exc['pagenumber'] = original_detail.get('pagenumber',0)


            extracted_date = result_or_exc.get("extracted_date_from_gemini")
            if extracted_date and extracted_date != "unknown" and re.match(r'^\d{2}-\d{2}-\d{4}$', extracted_date):
                all_extracted_dates_from_gemini.append(extracted_date)
            if result_or_exc.get("ministryName", "Unknown") == "Unknown":
                original_crop_detail = original_article_crops_map.get(current_unique_id_from_analysis)
                if original_crop_detail and original_crop_detail.get('path') and os.path.exists(original_crop_detail['path']):
                    try: os.remove(original_crop_detail['path'])
                    except Exception as e_rem: print(f"{log_prefix} Error removing img for 'Unknown' ministry article {current_unique_id_from_analysis}: {e_rem}")
                continue
            successfully_analyzed_metadata_for_upload.append(result_or_exc)


        # --- Step 5: Determine overall newspaper date ---
        determined_date_str = date_param
        if not (determined_date_str and re.match(r'^\d{2}-\d{2}-\d{4}$', determined_date_str)): determined_date_str = "unknown"
        if determined_date_str == "unknown" and all_extracted_dates_from_gemini: determined_date_str = all_extracted_dates_from_gemini[0]
        if determined_date_str == "unknown": determined_date_str = datetime.date.today().strftime("%d-%m-%Y")


        # --- Step 6: Parallel S3 Uploads ---
        upload_coroutines = []
        if successfully_analyzed_metadata_for_upload:
            time_s = time.monotonic()
            for article_to_upload in successfully_analyzed_metadata_for_upload:
                async def _upload_assets_helper(analysis_item, img_path, pub, ed, news_date, page_num, temp_dir_json, req_id, proc_pid):
                    log_prefix_upload = f"[{req_id}/{proc_pid}] Article {analysis_item.get('unique_article_id', 'unknown_upload')}:"
                    item_image_url, item_json_url = "UPLOAD_FAILED", "UPLOAD_FAILED"
                    try:
                        if img_path and os.path.exists(img_path):
                            item_image_url = await upload_file_to_s3(img_path, pub, ed, news_date, page_num)
                        else: print(f"{log_prefix_upload} Image path missing or invalid: {img_path}")
                        
                        analysis_item["image_url"] = item_image_url if not item_image_url.startswith("UPLOAD_FAILED:") else "UPLOAD_FAILED"
                        if analysis_item["image_url"] != "UPLOAD_FAILED": all_s3_file_urls_for_response.append(analysis_item["image_url"])

                        item_json_url = await save_analysis_json_and_upload(analysis_item, pub, ed, news_date, page_num, temp_dir_json)
                        analysis_item["ocr_output_url"] = item_json_url if not item_json_url.startswith("UPLOAD_FAILED:") else "UPLOAD_FAILED"
                        if analysis_item["ocr_output_url"] != "UPLOAD_FAILED": all_s3_file_urls_for_response.append(analysis_item["ocr_output_url"])

                        if analysis_item["image_url"] == "UPLOAD_FAILED" or analysis_item["ocr_output_url"] == "UPLOAD_FAILED":
                            analysis_item["error"] = (analysis_item.get("error", "") + " S3 Upload Failed.").strip()
                    except Exception as e_upload_helper:
                        analysis_item["error"] = (analysis_item.get("error", "") + f" S3 Upload Helper Exception: {e_upload_helper}").strip()
                        print(f"{log_prefix_upload} Exception in _upload_assets_helper: {e_upload_helper}")
                    finally:
                        if img_path and os.path.exists(img_path): # Ensure img_path is not None
                            try: os.remove(img_path)
                            except Exception as e_rem: print(f"{log_prefix_upload} Error removing local crop {img_path}: {e_rem}")
                    return analysis_item
                
                upload_coroutines.append(
                    _upload_assets_helper(
                        article_to_upload.copy(), article_to_upload.get("path"),
                        publication_name_param, edition_name_param, determined_date_str,
                        article_to_upload.get("pagenumber",0), task_temp_dir, request_id, process_pid
                    )
                )

            processed_articles_with_s3_urls = await asyncio.gather(*upload_coroutines, return_exceptions=True)
            for res_item_or_exc in processed_articles_with_s3_urls:
                if isinstance(res_item_or_exc, Exception): final_response_articles_list.append({"error": f"Upload coroutine failed: {str(res_item_or_exc)}"})
                else: final_response_articles_list.append(res_item_or_exc)
            print(f"{log_prefix} S3 Uploads took: {time.monotonic() - time_s:.2f}s.")
        
        final_response_articles_list.extend(analysis_error_metadata_for_response)
        
        total_orchestration_time = time.monotonic() - start_time
        print(f"{log_prefix} Orchestration finished in {total_orchestration_time:.2f} seconds.")

        return PDFProcessingResponse(
            publication=publication_name_param, edition=edition_name_param, date=determined_date_str,
            language=language_name_param, total_pages=total_pages,
            articles=final_response_articles_list,
            file_urls=", ".join(sorted(list(set(all_s3_file_urls_for_response)))),
            zoneName=zone_name_param
        ).model_dump()

    except Exception as e_orchestrate:
        total_orchestration_time = time.monotonic() - start_time
        print(f"{log_prefix} FATAL error in _orchestrate_pdf_processing ({total_orchestration_time:.2f}s): {e_orchestrate}")
        import traceback
        traceback.print_exc()
        return PDFProcessingResponse( publication=publication_name_param, edition=edition_name_param, date=date_param or "unknown", language=language_name_param, total_pages=0, articles=[{"error": f"Fatal orchestration error: {str(e_orchestrate)}"}], file_urls="").model_dump()


# Synchronous wrapper called by the Celery task (mostly same as before, ensure DPI/Quality are passed)
def process_newspaper_pdf_sync_caller(
    local_pdf_path: str,
    publicationName: str,
    editionName: str,
    date: Optional[str],
    languageName: str,
    zoneName: Optional[str],
    dpi: int,
    quality: int,
    resize_bool: bool
):
    task_specific_temp_dir = ""
    pid = os.getpid()
    try:
        task_specific_temp_dir = tempfile.mkdtemp(prefix=f"ocr_pipeline_sync_{uuid.uuid4().hex[:6]}_")
        print(f"Sync Caller (PID {pid}): Temp dir {task_specific_temp_dir} for {os.path.basename(local_pdf_path)}")
        
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError: # No current event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result_dict = loop.run_until_complete(
            _orchestrate_pdf_processing(
                local_pdf_path=local_pdf_path,
                publication_name_param=publicationName,
                edition_name_param=editionName,
                date_param=date,
                language_name_param=languageName,
                zone_name_param=zoneName,
                dpi_param=dpi,
                quality_param=quality,
                task_temp_dir=task_specific_temp_dir,
                resize_bool= resize_bool
            )
        )
        return result_dict
    except Exception as e_sync_caller:
        print(f"Sync Caller (PID {pid}): Error for {os.path.basename(local_pdf_path)}: {e_sync_caller}")
        import traceback; traceback.print_exc()
        return PDFProcessingResponse(publication=publicationName, edition=editionName, date=date or "unknown", language=languageName, total_pages=0, articles=[{"error": f"Sync caller error: {str(e_sync_caller)}"}], file_urls="").model_dump()
    finally:
        if task_specific_temp_dir and os.path.exists(task_specific_temp_dir):
            try:
                shutil.rmtree(task_specific_temp_dir)
                print(f"Sync Caller (PID {pid}): Cleaned temp dir {task_specific_temp_dir}")
            except Exception as cleanup_e:
                print(f"Sync Caller (PID {pid}): Error cleaning temp dir {task_specific_temp_dir}: {cleanup_e}")

# Function to process a single newspaper page image
def process_newspaper_page_image(
    image_path: str,
    publication_name: str,
    edition_name: Optional[str],
    date: Optional[str],
    language_name: str,
    zone_name: Optional[str],
    page_number: int
):
    """
    Process a single newspaper page image.
    This function is used for direct image processing from the crawler.
    
    Args:
        image_path: Path to the image file
        publication_name: Name of the publication
        edition_name: Name of the edition
        date: Date of the publication in DD-MM-YYYY format
        language_name: Language of the publication
        zone_name: Zone of the publication
        page_number: Page number
        
    Returns:
        Dictionary with processing results
    """
    task_specific_temp_dir = ""
    pid = os.getpid()
    try:
        task_specific_temp_dir = tempfile.mkdtemp(prefix=f"ocr_pipeline_direct_img_{uuid.uuid4().hex[:6]}_")
        print(f"Direct Image Processor (PID {pid}): Temp dir {task_specific_temp_dir} for {os.path.basename(image_path)}")
        
        # Create a specific subdirectory for this page's intermediate files and final crops
        page_processing_dir = os.path.join(task_specific_temp_dir, f"page_{page_number}")
        os.makedirs(page_processing_dir, exist_ok=True)
        
        # Generate a unique file prefix for article IDs
        file_prefix = f"{publication_name}_{date}_{page_number}"
        
        # Process the page image (segment and crop)
        # Since _process_single_page_segment_and_crop is an async function, we need to run it with the event loop
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:  # No current event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        article_crops = loop.run_until_complete(_process_single_page_segment_and_crop(
            full_page_image_path=image_path,
            page_number=page_number,
            file_prefix_for_ids=file_prefix,
            page_processing_base_temp_dir=task_specific_temp_dir,
            dpi_for_arcanum=DEFAULT_DPI,
            crop_jpeg_quality=DEFAULT_JPEG_QUALITY
        ))
        
        # Debug: Print article_crops info
        print(f"Direct Image Processor (PID {pid}): Found {len(article_crops)} article crops")
        for i, crop in enumerate(article_crops):
            print(f"Direct Image Processor (PID {pid}): Article crop {i+1} keys: {list(crop.keys())}")
        
        # Process each article crop
        processed_articles = []
        
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:  # No current event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Process all article crops in parallel
        # First, filter out invalid article crops
        valid_article_crops = []
        for article_crop in article_crops:
            if "path" not in article_crop:
                print(f"Error: Missing 'path' in article crop: {article_crop}")
                continue
            
            if "unique_article_id" not in article_crop:
                print(f"Error: Missing 'unique_article_id' in article crop: {article_crop}")
                continue
            
            if not os.path.exists(article_crop["path"]):
                print(f"Error: Image file does not exist: {article_crop['path']}")
                continue
                
            valid_article_crops.append(article_crop)
        
        # Create a list of coroutines for parallel processing
        analysis_coroutines = []
        for article_crop in valid_article_crops:
            analysis_coroutines.append(
                analyze_news_article_content(
                    article_crop,  # Pass the entire article_crop dictionary
                    language_name
                )
            )
        
        # Process all article crops in parallel
        if analysis_coroutines:
            print(f"Processing {len(analysis_coroutines)} article crops in parallel")
            analysis_results = loop.run_until_complete(asyncio.gather(*analysis_coroutines, return_exceptions=True))
            
            # Process the results
            for i, (article_crop, analysis_result) in enumerate(zip(valid_article_crops, analysis_results)):
                try:
                    # Handle exceptions
                    if isinstance(analysis_result, Exception):
                        print(f"Error processing article {article_crop['unique_article_id']}: {analysis_result}")
                        continue
                    
                    # Skip if the article was classified as an advertisement
                    if analysis_result is None:
                        print(f"Skipping article {article_crop['unique_article_id']} as it was classified as an advertisement")
                        continue
                    
                    # Add article metadata
                    article_result = {
                        "unique_article_id": article_crop["unique_article_id"],
                        "pagenumber": page_number,
                        "language": language_name,
                        **analysis_result
                    }
                
                    # Upload the article image to S3 if needed - we'll do this in parallel later
                    if config.s3_client and config.AWS_S3_BUCKET_NAME_CONFIG:
                        # Format date as YYYY-MM-DD if it's not already
                        formatted_date = date if date else "unknown_date"
                        try:
                            if date and len(date.split('-')) == 3:
                                # Date is already in YYYY-MM-DD format
                                pass
                            elif date and len(date.split('/')) == 3:
                                # Convert DD/MM/YYYY to YYYY-MM-DD
                                day, month, year = date.split('/')
                                formatted_date = f"{year}-{month}-{day}"
                            elif date:
                                # Try to handle other date formats
                                import datetime
                                # Try to parse the date
                                for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y.%m.%d"]:
                                    try:
                                        dt = datetime.datetime.strptime(date, fmt)
                                        formatted_date = dt.strftime("%Y-%m-%d")
                                        break
                                    except ValueError:
                                        continue
                        except Exception as e:
                            print(f"Error formatting date '{date}': {e}. Using original value.")
                            formatted_date = date if date else "unknown_date"
                        
                        # Store the upload parameters for later
                        article_result["s3_upload_params"] = {
                            "file_path": article_crop["path"],
                            "publication_name": publication_name,
                            "edition_name": edition_name if edition_name else "default_edition",
                            "date_str": formatted_date,
                            "page_number": page_number,
                            "object_name_override": f"{article_crop['unique_article_id']}.jpg"
                        }
                    
                    processed_articles.append(article_result)
                except Exception as e:
                    print(f"Error processing article crop {article_crop.get('unique_article_id', 'unknown')}: {e}")
                    processed_articles.append({
                        "unique_article_id": article_crop.get("unique_article_id", f"error_{uuid.uuid4().hex[:8]}"),
                        "pagenumber": page_number,
                        "language": language_name,
                        "error": f"Article processing error: {str(e)}"
                    })
        
        # Now perform all S3 uploads in parallel
        if config.s3_client and config.AWS_S3_BUCKET_NAME_CONFIG:
            # Collect all articles that need S3 upload
            s3_upload_tasks = []
            for i, article in enumerate(processed_articles):
                if "s3_upload_params" in article:
                    s3_upload_tasks.append((i, upload_file_to_s3(**article["s3_upload_params"])))
            
            if s3_upload_tasks:
                print(f"Direct Image Processor (PID {pid}): Starting {len(s3_upload_tasks)} parallel S3 uploads")
                # Run all uploads in parallel
                s3_results = loop.run_until_complete(asyncio.gather(*[task for _, task in s3_upload_tasks], return_exceptions=True))
                
                # Process results
                for (article_index, _), s3_result in zip(s3_upload_tasks, s3_results):
                    if isinstance(s3_result, Exception):
                        print(f"S3 upload error for article {processed_articles[article_index]['unique_article_id']}: {s3_result}")
                    else:
                        # Add the S3 URL to the article
                        processed_articles[article_index]["image_url"] = s3_result
                        # Remove the upload params as they're no longer needed
                        del processed_articles[article_index]["s3_upload_params"]
        
        return {
            "publication": publication_name,
            "edition": edition_name,
            "date": date,
            "language": language_name,
            "zoneName": zone_name,
            "page_number": page_number,
            "articles": processed_articles,
            "total_articles": len(processed_articles)
        }
    
    except Exception as e:
        print(f"Direct Image Processor (PID {pid}): Error for {os.path.basename(image_path)}: {e}")
        import traceback; traceback.print_exc()
        return {
            "publication": publication_name,
            "edition": edition_name,
            "date": date or "unknown",
            "language": language_name,
            "zoneName": zone_name,
            "page_number": page_number,
            "articles": [{"error": f"Direct image processing error: {str(e)}"}],
            "total_articles": 0
        }
    finally:
        if task_specific_temp_dir and os.path.exists(task_specific_temp_dir):
            try:
                shutil.rmtree(task_specific_temp_dir)
                print(f"Direct Image Processor (PID {pid}): Cleaned temp dir {task_specific_temp_dir}")
            except Exception as cleanup_e:
                print(f"Direct Image Processor (PID {pid}): Error cleaning temp dir {task_specific_temp_dir}: {cleanup_e}")