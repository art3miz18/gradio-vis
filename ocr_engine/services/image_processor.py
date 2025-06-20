# ocr_engine/services/image_processor.py
import os
import uuid
from typing import List, Dict, Any
from PIL import Image
# import numpy as np # Only if needed for advanced image ops not done by PIL

# This function is now specifically for cropping based on Arcanum's output
def crop_articles_from_segmentation_data(
    original_page_pil_image: Image.Image,
    arcanum_segmentation_response: Dict[str, Any],
    page_number: int,
    file_prefix_for_ids: str,
    article_crops_output_dir: str, # e.g., task_temp_dir/page_outputs/page_N/article_crops
    crop_jpeg_quality: int = 85
) -> List[Dict[str, Any]]:
    """
    Crops articles from a page based on Arcanum segmentation data.
    Filters out "articles" predominantly labeled as "Advertising" by Arcanum.
    Saves valid article crops to article_crops_output_dir.
    Returns list of dicts, each with info about a saved crop.
    """
    os.makedirs(article_crops_output_dir, exist_ok=True)
    extracted_article_infos = []
    pid = os.getpid() # For logging

    if "articles" not in arcanum_segmentation_response or not isinstance(arcanum_segmentation_response["articles"], list):
        print(f"[{pid}] ImgProc Page {page_number}: No 'articles' list in Arcanum response or it's empty/invalid.")
        return []

    page_width, page_height = original_page_pil_image.size

    for i, arcanum_article_obj in enumerate(arcanum_segmentation_response["articles"]):
        article_blocks = arcanum_article_obj.get("blocks", [])
        if not article_blocks:
            # print(f"[{pid}] ImgProc Page {page_number}, Arcanum Article {i+1}: No blocks. Skipping.")
            continue

        # Check for "Advertising" label from Arcanum
        is_arcanum_ad = False
        # Consider an "article" an ad if ALL its blocks are labeled "Advertising"
        # or if the article object itself has a predominant "Advertising" label (if Arcanum provides that)
        # For now, simple check: if any block is 'Advertising', we might scrutinize more.
        # A better check: if a significant number of blocks are 'Advertising'.
        ad_block_count = sum(1 for block in article_blocks if block.get("label", "").lower() == "advertising")
        
        # Threshold: if more than 50% of blocks are ads, or if it's a single block and it's an ad.
        if total_blocks := len(article_blocks) > 0: # Python 3.8+ walrus operator
             if (ad_block_count / total_blocks > 0.5) or (total_blocks == 1 and ad_block_count == 1) :
                is_arcanum_ad = True
        
        if is_arcanum_ad:
            # print(f"[{pid}] ImgProc Page {page_number}, Arcanum Article {i+1}: Identified as 'Advertising' by Arcanum label. Skipping.")
            continue

        # Aggregate bounds for non-ad articles
        all_block_coords_for_this_article = []
        for block in article_blocks:
            # We already decided this isn't primarily an ad, so process all its blocks for bounds
            if "bounds" in block and len(block["bounds"]) == 4:
                x_min_norm, y_min_norm, x_max_norm, y_max_norm = block["bounds"]
                # Convert to absolute pixel values
                x_min_abs = int(x_min_norm * page_width)
                y_min_abs = int(y_min_norm * page_height)
                x_max_abs = int(x_max_norm * page_width)
                y_max_abs = int(y_max_norm * page_height)
                
                if (x_max_abs - x_min_abs) > 5 and (y_max_abs - y_min_abs) > 5: # Min dimensions for a block
                    all_block_coords_for_this_article.append((x_min_abs, y_min_abs, x_max_abs, y_max_abs))
        
        if not all_block_coords_for_this_article:
            # print(f"[{pid}] ImgProc Page {page_number}, Arcanum Article {i+1}: No valid block coordinates. Skipping.")
            continue

        article_x_min = min(b[0] for b in all_block_coords_for_this_article)
        article_y_min = min(b[1] for b in all_block_coords_for_this_article)
        article_x_max = max(b[2] for b in all_block_coords_for_this_article)
        article_y_max = max(b[3] for b in all_block_coords_for_this_article)

        if article_x_min >= article_x_max or article_y_min >= article_y_max or \
           (article_x_max - article_x_min) < 10 or (article_y_max - article_y_min) < 10: # Min article dimensions
            # print(f"[{pid}] ImgProc Page {page_number}, Arcanum Article {i+1}: Combined bounds too small or invalid. Skipping.")
            continue
        
        try:
            article_crop_pil = original_page_pil_image.crop((article_x_min, article_y_min, article_x_max, article_y_max))
            
            article_uuid_part = uuid.uuid4().hex[:6]
            # This ID should be unique for each *potential article crop* passed to Gemini
            unique_article_id = f"{file_prefix_for_ids}_p{page_number:03d}_crop{i+1:03d}_{article_uuid_part}"
            
            article_crop_filename = f"{unique_article_id}.jpg" # Use the unique ID in filename
            article_crop_path = os.path.join(article_crops_output_dir, article_crop_filename)
            
            article_crop_pil.save(article_crop_path, "JPEG", quality=crop_jpeg_quality)
            
            extracted_article_infos.append({
                "unique_article_id": unique_article_id,
                "pagenumber": page_number,
                "path": article_crop_path, # Local path to the saved cropped article image
                "bounds_on_page_pixels": (article_x_min, article_y_min, article_x_max, article_y_max),
                "arcanum_was_ad": False # Flag that it passed Arcanum's ad filter
            })
        except Exception as e_crop_save:
            print(f"[{pid}] ImgProc Page {page_number}, Arcanum Article {i+1}: Error saving crop {unique_article_id}: {e_crop_save}")
            
    return extracted_article_infos