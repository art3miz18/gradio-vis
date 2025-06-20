import os
import uuid
import subprocess
import gc
import time
import tempfile
import logging
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageFile

# Allow truncated images to be processed
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(processName)s:%(process)d] [%(threadName)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger("pdf_processor")

def convert_pdf_to_images_with_mutool(
    pdf_path: str,
    task_temp_dir: str,
    dpi: int = 200,
    jpeg_quality: int = 80,
    use_resize: bool = True,
    max_dimension: int = 3000,
    chunk_size: int = 5,
    mutool_timeout: int = 60,
    max_retries: int = 2,
    memory_limit_mb: int = 1000
) -> List[str]:
    """
    Fast and memory-efficient PDF to JPEG conversion using MuPDF (mutool draw).
    Uses page ranges and parallel processing for better performance.

    Args:
        pdf_path: Input PDF file path.
        task_temp_dir: Temp directory to store intermediate/output files.
        dpi: Output DPI resolution.
        jpeg_quality: JPEG quality (1–100).
        use_resize: Whether to resize oversized images.
        max_dimension: Max width or height allowed (if resizing).
        chunk_size: Number of pages to process in each mutool call.
        mutool_timeout: Timeout for each mutool subprocess call in seconds.
        max_retries: Maximum number of retries for failed conversions.
        memory_limit_mb: Memory limit for mutool process in MB.

    Returns:
        List of JPEG image paths.
    """
    pid = os.getpid()
    image_output_dir = os.path.join(task_temp_dir, f"pdf_pages_{uuid.uuid4().hex[:8]}")
    Path(image_output_dir).mkdir(parents=True, exist_ok=True)
    
    logger.info(f"[{pid}] Starting conversion of {os.path.basename(pdf_path)}")
    
    # Get PDF page count using mutool info
    try:
        page_count = get_pdf_page_count(pdf_path)
        logger.info(f"[{pid}] PDF has {page_count} pages")
    except Exception as e:
        logger.error(f"[{pid}] Failed to get page count: {e}")
        page_count = 100  # Fallback assumption
    
    # Create page ranges for chunked processing
    page_ranges = [(i, min(i+chunk_size-1, page_count-1)) 
                 for i in range(0, page_count, chunk_size)]
    
    final_jpeg_paths = []
    failed_ranges = []

    # Process page ranges
    for attempt in range(max_retries + 1):
        if not page_ranges:
            break
            
        logger.info(f"[{pid}] Processing {len(page_ranges)} page ranges (attempt {attempt+1})")
        current_ranges = page_ranges
        page_ranges = []
        
        # Process each range with resource limits
        for start_page, end_page in current_ranges:
            try:
                # Create range-specific output directory
                range_dir = os.path.join(image_output_dir, f"range_{start_page}_{end_page}")
                Path(range_dir).mkdir(exist_ok=True)
                
                logger.info(f"[{pid}] Processing pages {start_page+1}-{end_page+1}")
                
                # Set resource limits and run mutool
                result = run_mutool_with_limits(
                    pdf_path, 
                    range_dir,
                    start_page, 
                    end_page, 
                    dpi, 
                    timeout=mutool_timeout,
                    memory_limit_mb=memory_limit_mb
                )
                
                if result:
                    # Process the resulting PNGs to JPEGs
                    jpeg_paths = process_pngs_to_jpegs(
                        range_dir, 
                        jpeg_quality, 
                        use_resize, 
                        max_dimension
                    )
                    final_jpeg_paths.extend(jpeg_paths)
                    
                    # Force garbage collection after each chunk
                    gc.collect()
                else:
                    # Mark range for retry
                    failed_ranges.append((start_page, end_page))
                    
            except Exception as e:
                logger.error(f"[{pid}] Error processing pages {start_page+1}-{end_page+1}: {e}")
                failed_ranges.append((start_page, end_page))
        
        # Update page ranges with failed ones for next attempt
        page_ranges = failed_ranges
        failed_ranges = []
    
    # Check for remaining failed ranges after all retries
    if page_ranges:
        logger.warning(f"[{pid}] {len(page_ranges)} page ranges failed after all retries")
    
    logger.info(f"[{pid}] Successfully converted {len(final_jpeg_paths)} pages")
    return sorted(final_jpeg_paths)

def get_pdf_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF using mutool info."""
    try:
        result = subprocess.run(
            ["mutool", "info", pdf_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        for line in result.stdout.splitlines():
            if "Pages:" in line:
                return int(line.split(":")[1].strip())
        
        # Default fallback if Pages line not found
        return 1
    except Exception as e:
        logger.error(f"Error getting PDF page count: {e}")
        raise

def run_mutool_with_limits(
    pdf_path: str, 
    output_dir: str, 
    start_page: int, 
    end_page: int, 
    dpi: int,
    timeout: int = 60,
    memory_limit_mb: int = 1000
) -> bool:
    """Run mutool with resource limits and page range."""
    
    # Get the proper shell command to set memory limit based on platform
    limit_cmd = f"ulimit -v {memory_limit_mb * 1024} && " if os.name == "posix" else ""
    
    # Build the mutool command
    mutool_cmd = [
        "mutool", "draw",
        "-r", str(dpi),
        "-o", os.path.join(output_dir, "page-%03d.png"),
        pdf_path,
        str(start_page+1)+"-"+str(end_page+1)  # mutool uses 1-based page numbers
    ]
    
    try:
        # For Unix systems, use shell=True to apply memory limits
        if os.name == "posix":
            full_cmd = f"{limit_cmd}{' '.join(mutool_cmd)}"
            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        else:
            # For Windows and other systems
            proc = subprocess.Popen(
                mutool_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
        # Wait for process with timeout
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            if proc.returncode != 0:
                logger.error(f"mutool failed: {stderr.decode('utf-8', errors='ignore')}")
                return False
            return True
        except subprocess.TimeoutExpired:
            # Kill process if timeout
            logger.warning(f"mutool timeout after {timeout}s for pages {start_page+1}-{end_page+1}")
            kill_process_tree(proc.pid)
            return False
    
    except Exception as e:
        logger.error(f"Error running mutool: {e}")
        return False

def kill_process_tree(pid):
    """Kill a process and all its children."""
    if os.name == "nt":  # Windows
        subprocess.call(['taskkill', '/F', '/T', '/PID', str(pid)])
    else:  # Unix
        try:
            parent = subprocess.Popen(f"ps -o pid --ppid {pid} --noheaders", 
                               shell=True, stdout=subprocess.PIPE)
            children = parent.stdout.read().decode().strip().split('\n')
            
            # Kill children first
            for child in children:
                if child:
                    os.kill(int(child), signal.SIGTERM)
            
            # Kill parent
            os.kill(pid, signal.SIGTERM)
        except:
            pass

def process_pngs_to_jpegs(
    directory: str, 
    jpeg_quality: int, 
    use_resize: bool, 
    max_dimension: int
) -> List[str]:
    """Convert PNG files to JPEGs with parallel processing."""
    
    png_files = sorted(Path(directory).glob("page-*.png"))
    jpeg_paths = []
    
    # Use ThreadPoolExecutor for parallel image processing
    with ThreadPoolExecutor(max_workers=min(4, len(png_files))) as executor:
        futures = {
            executor.submit(
                convert_png_to_jpeg, 
                png_file, 
                jpeg_quality, 
                use_resize, 
                max_dimension
            ): png_file for png_file in png_files
        }
        
        for future in as_completed(futures):
            png_file = futures[future]
            try:
                jpeg_path = future.result()
                if jpeg_path:
                    jpeg_paths.append(jpeg_path)
            except Exception as e:
                logger.error(f"Error converting {png_file.name}: {e}")
                
    return jpeg_paths

def convert_png_to_jpeg(
    png_file: Path, 
    jpeg_quality: int, 
    use_resize: bool, 
    max_dimension: int
) -> Optional[str]:
    """Convert a single PNG to JPEG with error handling."""
    try:
        # Open with a timeout mechanism
        start_time = time.time()
        img = Image.open(png_file)
        
        # Check for oversized images
        if use_resize and (img.width > max_dimension or img.height > max_dimension):
            img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        
        # Generate unique output filename
        jpeg_filename = f"{png_file.stem}_{uuid.uuid4().hex[:6]}.jpg"
        jpeg_path = str(png_file.parent / jpeg_filename)
        
        # Save as JPEG
        img.save(jpeg_path, "JPEG", quality=jpeg_quality, optimize=True)
        
        # Clean up
        img.close()
        png_file.unlink(missing_ok=True)
        
        return jpeg_path
    except Exception as e:
        logger.error(f"PNG to JPEG conversion error for {png_file.name}: {e}")
        # Try to clean up the PNG even if conversion failed
        try:
            if png_file.exists():
                png_file.unlink()
        except:
            pass
        return None