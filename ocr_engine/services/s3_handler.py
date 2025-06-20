import os
import re
import json
import uuid
import asyncio
import mimetypes
from typing import Dict, Any, Optional, List

from config import s3_client, AWS_S3_BUCKET_NAME_CONFIG, AWS_REGION_CONFIG # Import S3 client and config

async def upload_file_to_s3(file_path: str, publication_name: str, edition_name: str, date_str: str, page_number: int, object_name_override: Optional[str] = None) -> str:
    """Uploads a file to S3."""
    if not s3_client or not AWS_S3_BUCKET_NAME_CONFIG:
        print(f"S3 Upload skipped for {file_path}: S3 client or bucket name not configured.")
        return "" # Return empty string or specific error code

    cleaned_pub = re.sub(r'[^\w.\-/]', '_', publication_name).replace(' ', '_')
    cleaned_ed = re.sub(r'[^\w.\-/]', '_', edition_name).replace(' ', '_')
    
    if object_name_override:
        file_name_for_s3 = object_name_override
    else:
        file_name_for_s3 = os.path.basename(file_path)

    s3_key = f"digital/{cleaned_pub}/{cleaned_ed}/{date_str}/{page_number:03d}/{file_name_for_s3}"

    try:
        content_type, _ = mimetypes.guess_type(file_path)
        content_type = content_type or 'application/octet-stream'
        extra_args = {'ContentType': content_type}
        if content_type.startswith('text/'): # For JSON files
            extra_args['ContentEncoding'] = 'utf-8'

        print(f"Uploading {file_path} to S3 key: {s3_key}")
        await asyncio.to_thread(
            s3_client.upload_file,
            Filename=file_path, Bucket=AWS_S3_BUCKET_NAME_CONFIG, Key=s3_key, ExtraArgs=extra_args
        )
        url = f"https://{AWS_S3_BUCKET_NAME_CONFIG}.s3.{AWS_REGION_CONFIG}.amazonaws.com/{s3_key}"
        print(f"Uploaded to S3: {url}")
        return url
    except Exception as e:
        print(f"Upload failed for {file_path} to S3 key {s3_key}: {e}")
        import traceback
        traceback.print_exc()
        return f"UPLOAD_FAILED:{e}"


async def save_analysis_json_and_upload(
    analysis_data: Dict[str, Any], 
    publication_name: str, 
    edition_name: str, 
    date_str: str, 
    page_number: int, 
    task_temp_dir: str
) -> str:
    """Saves analysis data as JSON locally and uploads it to S3."""
    if not s3_client or not AWS_S3_BUCKET_NAME_CONFIG:
        print(f"S3 Upload skipped for analysis result: S3 client or bucket name not configured.")
        return ""

    json_output_dir = os.path.join(task_temp_dir, "analysis_json_outputs")
    os.makedirs(json_output_dir, exist_ok=True)
    
    unique_article_id = analysis_data.get("unique_article_id", f"unknown_article_{uuid.uuid4().hex[:6]}")
    json_file_name = f"{unique_article_id}_analysis.json"
    local_json_path = os.path.join(json_output_dir, json_file_name)

    try:
        # Ensure data for JSON is clean (e.g. no Path objects if they snuck in)
        serializable_data = {k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v 
                             for k, v in analysis_data.items()}

        with open(local_json_path, "w", encoding="utf-8") as f:
            json.dump(serializable_data, f, ensure_ascii=False, indent=2) # default=str removed for more control
        
        s3_url = await upload_file_to_s3(
            local_json_path, 
            publication_name, 
            edition_name, 
            date_str, 
            page_number,
            object_name_override=json_file_name # Use the same name in S3
        )
        return s3_url

    except Exception as e:
        print(f"Error saving/uploading analysis JSON for {unique_article_id}: {e}")
        import traceback
        traceback.print_exc()
        return f"UPLOAD_FAILED:LocalOrUploadError:{e}"
    finally:
        if os.path.exists(local_json_path):
            try:
                os.remove(local_json_path) # Clean up local JSON after attempt
                # print(f"Cleaned up temporary analysis JSON file: {local_json_path}")
            except Exception as cleanup_e:
                 print(f"Error cleaning up temporary analysis JSON file {local_json_path}: {cleanup_e}")