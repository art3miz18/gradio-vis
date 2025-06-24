# ocr_engine/services/content_analyzer.py
import os
import base64
import json
import re
import asyncio
import uuid # Ensure uuid is imported
from typing import Dict, Any, Optional

from config import (
    get_configured_ad_checker_model,
    get_configured_content_analyzer_model,    # For image-based newspaper articles
    get_configured_digital_text_analyzer_model, # For text-based digital articles
    get_configured_text_ad_checker_model,
    AD_CHECK_PROMPT

)
from utils.json_utils import extract_json_from_response

# --- ADVERTISEMENT CHECK (for images) ---
async def _is_advertisement_gemini_async(image_path: str) -> bool:
    pid = os.getpid()
    current_ad_checker_model = get_configured_ad_checker_model()
    if not current_ad_checker_model: print(f"[{pid}] AdCheck: Model N/A."); return False
    if not os.path.exists(image_path): print(f"[{pid}] AdCheck: Image path N/A."); return False
    try:
        # Resize the image to reduce size before sending to Gemini
        from PIL import Image
        import io
        
        # Open the image and resize it
        with Image.open(image_path) as img:
            # For ad detection, we can use a much smaller image
            # Ad detection doesn't need high resolution to determine if something is an ad
            max_dimension = 400  # Even smaller for ad detection
            if img.width > max_dimension or img.height > max_dimension:
                print(f"[{pid}] AdCheck: Resizing image from {img.width}x{img.height} to max dimension {max_dimension}")
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            
            # Save to a BytesIO object
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75)
            buffer.seek(0)
            
            # Encode as base64
            img_b64 = base64.b64encode(buffer.read()).decode("utf-8")
        
        img_data_part = {"mime_type": "image/jpeg", "data": img_b64}
        prompt_parts = [AD_CHECK_PROMPT, img_data_part]
        resp_obj = await asyncio.to_thread(current_ad_checker_model.generate_content, prompt_parts)
        if resp_obj and resp_obj.text:
            res_dict = extract_json_from_response(resp_obj.text)
            return res_dict.get("is_advertisement", False) if isinstance(res_dict, dict) else False
        return False
    except Exception as e: print(f"[{pid}] AdCheck Ex: {e}"); return False

# --- CONTENT ANALYSIS FOR NEWSPAPER ARTICLE IMAGES (Flow 1 & 2) ---
async def analyze_news_article_content(
    article_crop_meta: Dict[str, Any], # Expects "path" to an image, "unique_article_id", "pagenumber"
    language_name_param: str # Overall language of the newspaper
) -> Optional[Dict[str, Any]]:
    pid = os.getpid()
    unique_article_id = article_crop_meta.get('unique_article_id', f'img_art_{pid}_{uuid.uuid4().hex[:4]}')
    page_number = article_crop_meta.get('pagenumber', 0)
    article_crop_path = article_crop_meta.get('path')
    base_error_return = {"unique_article_id": unique_article_id, "pagenumber": page_number, "path": article_crop_path}

    if not article_crop_path or not os.path.exists(article_crop_path):
        print(f"[{pid}] ImgAnalyzer: Crop file not found for {unique_article_id}.")
        return {**base_error_return, "error": "Article crop image file not found."}

    try:
        # DISABLED: Secondary Ad Check specific to this image crop
        # is_ad = await _is_advertisement_gemini_async(article_crop_path)
        # if is_ad:
        #     print(f"[{pid}] ImgAnalyzer: Article {unique_article_id} classified as AD by Gemini. Skipping.")
        #     if os.path.exists(article_crop_path):
        #         try: os.remove(article_crop_path)
        #         except Exception as e_rem: print(f"[{pid}] ImgAnalyzer: Error cleaning ad image {article_crop_path}: {e_rem}")
        #     return None # Signal ad

        current_image_content_model = get_configured_content_analyzer_model()
        if not current_image_content_model:
            print(f"[{pid}] ImgAnalyzer: Image content analysis model NOT AVAILABLE for {unique_article_id}.")
            return {**base_error_return, "error": "Image content analysis model not configured."}

        # Resize the image to reduce size before sending to Gemini
        from PIL import Image
        import io
        
        # Open the image and resize it if needed
        with Image.open(article_crop_path) as img:
            # For OCR, we need to maintain a higher resolution to ensure text is readable
            # Only resize if the image is extremely large
            max_dimension = 2000  # Higher resolution for OCR
            if img.width > max_dimension or img.height > max_dimension:
                print(f"[{pid}] ImgAnalyzer: Resizing large image from {img.width}x{img.height} to max dimension {max_dimension}")
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            
            # Save to a BytesIO object
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            
            # Encode as base64
            article_base64 = base64.b64encode(buffer.read()).decode("utf-8")
        
        image_data_for_main_analysis = {"mime_type": "image/jpeg", "data": article_base64}
        
        # System instruction is part of current_image_content_model
        try:
            gemini_response_object = await asyncio.to_thread(
                current_image_content_model.generate_content,
                contents=[image_data_for_main_analysis]
            )
            
            # Handle the response more carefully
            if gemini_response_object and gemini_response_object.candidates and len(gemini_response_object.candidates) > 0:
                # Get the first candidate's content
                candidate = gemini_response_object.candidates[0]
                if candidate.content and candidate.content.parts:
                    gemini_response_text = candidate.content.parts[0].text
                else:
                    gemini_response_text = None
            else:
                gemini_response_text = None
                
            if not gemini_response_text:
                print(f"[{pid}] ImgAnalyzer: Main Gemini image analysis returned empty text for {unique_article_id}.")
                return {**base_error_return, "error": "Gemini image analysis returned empty text."}
        except ValueError as ve:
            print(f"[{pid}] ImgAnalyzer: ValueError in Gemini API for {unique_article_id}: {str(ve)}")
            return {**base_error_return, "error": f"Gemini API ValueError: {str(ve)}"}
        except Exception as e:
            print(f"[{pid}] ImgAnalyzer: Exception in Gemini API for {unique_article_id}: {str(e)}")
            return {**base_error_return, "error": f"Gemini API error: {str(e)}"}
        
        gemini_result_dict = extract_json_from_response(gemini_response_text)

        if not isinstance(gemini_result_dict, dict) or gemini_result_dict.get("error"):
            err = gemini_result_dict.get("error", "JSON parsing failed") if isinstance(gemini_result_dict, dict) else f"JSON parsing failed {type(gemini_result_dict)}"
            raw = gemini_result_dict.get('raw_response', gemini_response_text[:200]) if isinstance(gemini_result_dict, dict) else gemini_response_text[:200]
            print(f"[{pid}] ImgAnalyzer: JSON parsing error for {unique_article_id}. Error: {err}. Raw: {raw}")
            return {**base_error_return, "error": err, "raw_gemini_response_snippet": raw}

        # Populate all fields for the ProcessedArticle-like structure expected by pipeline_logic
        return {
            "unique_article_id": unique_article_id,
            "pagenumber": page_number,
            "language": gemini_result_dict.get("language", language_name_param),
            "heading": gemini_result_dict.get("heading", ""),
            "content": gemini_result_dict.get("content", ""),
            "english_heading": gemini_result_dict.get("english_heading", ""),
            "english_content": gemini_result_dict.get("english_content", ""),
            "english_summary": gemini_result_dict.get("english_summary", ""), # This becomes "summary" for Node
            "sentiment": gemini_result_dict.get("sentiment", "NEUTRAL").upper(),
            "ministryName": gemini_result_dict.get("ministries", [{}])[0].get("ministry", "Unknown") if gemini_result_dict.get("ministries") else "Unknown",
            "AdditionMinisrtyName": [m.get("ministry") for m in gemini_result_dict.get("ministries", [])[1:] if isinstance(m,dict) and m.get("ministry")],
            "extracted_date_from_gemini": gemini_result_dict.get("date", "unknown"), # Used by pipeline_logic for overall date
            "path": article_crop_path # For S3 upload reference by pipeline_logic
        }
    except Exception as e:
        print(f"[{pid}] ImgAnalyzer: Unexpected error for article {unique_article_id}: {type(e).__name__} - {e}")
        import traceback; traceback.print_exc()
        return {**base_error_return, "error": f"Unexpected image analysis error: {str(e)}"}


# --- NEW: CONTENT ANALYSIS FOR DIGITAL TEXT (Flow 3) ---
async def analyze_digital_text_content(
    text_content: str,
    original_language: Optional[str],
    original_heading: Optional[str] = None
) -> Dict[str, Any]: # Returns a dictionary matching Gemini's JSON structure for text
    pid = os.getpid()
    log_prefix = f"[{pid}] DigitalTextAnalyzer"
    
    # 0. Inline textual ad check
    ad_model = get_configured_text_ad_checker_model()
    if ad_model:
        ad_input = text_content
        ad_resp = await asyncio.to_thread(
            ad_model.generate_content,
            contents=ad_input
        )
        ad_text = ad_resp.text if hasattr(ad_resp, 'text') else ''
        ad_json = extract_json_from_response(ad_text)
        if isinstance(ad_json, dict) and ad_json.get("is_advertisement"):
            print(f"{log_prefix}: Classified as advertisement (confidence: {ad_json.get('confidence')})")
            return {"error": "advertisement_filtered"}
    else:
        print(f"{log_prefix}: Ad checker model unavailable; skipping ad filter.")


    current_text_model = get_configured_digital_text_analyzer_model()
    if not current_text_model:
        print(f"{log_prefix}: Digital text analysis model NOT AVAILABLE.")
        return {"error": "Digital text analysis model not configured."}

    # Construct the prompt for the text model
    # The system instruction is part of current_text_model.
    prompt_parts = []
    if original_heading: prompt_parts.append(f"Original Article Heading: {original_heading}\n")
    if original_language: prompt_parts.append(f"Original Article Language: {original_language}\n")
    prompt_parts.append("\nArticle Content to Analyze:\n---\n")
    prompt_parts.append(text_content)
    prompt_parts.append("\n---\nPlease provide your analysis in the specified JSON format based on the system instruction.")
    full_prompt_for_text_model = "".join(prompt_parts)

    # print(f"{log_prefix}: Sending text (len {len(full_prompt_for_text_model)}) to Gemini text model...")
    try:
        response_object = await asyncio.to_thread(
            current_text_model.generate_content,
            contents=[full_prompt_for_text_model]
        )
        response_text = response_object.text if response_object and hasattr(response_object, 'text') else None
        
        if not response_text:
            print(f"{log_prefix}: Gemini text analysis returned empty text.")
            return {"error": "Gemini text analysis returned empty text."}

        analysis_result_dict = extract_json_from_response(response_text)

        
        
        # The keys returned by this function should match what analyze_s3_digital_article_json_task expects
        # to build the NodeNewsItemPayload.
        # It should match the JSON structure defined in DIGITAL_TEXT_ANALYSIS_SYSTEM_INSTRUCTION.
        return {
            "language": analysis_result_dict.get("language", original_language),
            "english_heading": analysis_result_dict.get("english_heading"), # From Gemini
            "english_content": analysis_result_dict.get("english_content"), # From Gemini
            "english_summary": analysis_result_dict.get("english_summary"), # From Gemini
            "sentiment": analysis_result_dict.get("sentiment", "NEUTRAL").upper(), # From Gemini
            "ministryName": analysis_result_dict.get("ministries", [{}])[0].get("ministry", "Unknown") if analysis_result_dict.get("ministries") else "Unknown",
            "AdditionMinisrtyName": [m.get("ministry") for m in analysis_result_dict.get("ministries", [])[1:] if isinstance(m, dict) and m.get("ministry")],
            "date_from_text": analysis_result_dict.get("date_from_text", "unknown"), # From Gemini
            # Include original heading/content if the text system prompt asks for them for verification
            "original_heading_provided": analysis_result_dict.get("original_heading_provided", original_heading),
            "original_content_provided_snippet": analysis_result_dict.get("original_content_provided_snippet"),
        }
    except Exception as e:
        print(f"{log_prefix}: Error during digital text analysis: {type(e).__name__} - {e}")
        import traceback; traceback.print_exc()
        return {"error": f"Digital text analysis failed: {str(e)}"}