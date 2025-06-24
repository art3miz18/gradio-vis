# ocr_engine/progress_tracker.py
import json
import time
from typing import Dict, List, Optional, Any
import redis
import os
from dataclasses import dataclass, asdict, field
from enum import Enum

class ProcessingStep(Enum):
    INITIALIZING = "initializing"
    PDF_CONVERSION = "pdf_conversion"
    PAGE_SEGMENTATION = "page_segmentation"
    ARTICLE_ANALYSIS = "article_analysis"
    UPLOADING_RESULTS = "uploading_results"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class StepProgress:
    step: ProcessingStep
    progress_percent: int
    message: str
    start_time: float
    end_time: Optional[float] = None
    duration: Optional[float] = None
    details: Optional[Dict[str, Any]] = None

@dataclass
class ProcessingStatus:
    task_id: str
    current_step: ProcessingStep
    overall_progress: int
    total_pages: int
    processed_pages: int
    total_articles: int
    processed_articles: int
    start_time: float
    steps: List[StepProgress]
    images: List[Dict[str, Any]] = field(default_factory=list)  # {"url": "...", "page": 1}
    segmentations: List[Dict[str, Any]] = field(default_factory=list)  # Segmentation results
    articles: List[Dict[str, Any]] = field(default_factory=list)  # Final analyzed articles
    errors: List[Dict[str, Any]] = field(default_factory=list)

class ProgressTracker:
    def __init__(self, task_id: str, redis_url: str = None):
        self.task_id = task_id
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis_client = None
        self.status = ProcessingStatus(
            task_id=task_id,
            current_step=ProcessingStep.INITIALIZING,
            overall_progress=0,
            total_pages=0,
            processed_pages=0,
            total_articles=0,
            processed_articles=0,
            start_time=time.time(),
            steps=[],
            images=[],
            segmentations=[],
            articles=[],
            errors=[]
        )
        self._init_redis()
    
    def _init_redis(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(self.redis_url)
            self.redis_client.ping()
            print(f"ProgressTracker[{self.task_id}]: Redis connected")
        except Exception as e:
            print(f"ProgressTracker[{self.task_id}]: Redis connection failed: {e}")
            self.redis_client = None
    
    def start_step(self, step: ProcessingStep, message: str, details: Dict[str, Any] = None):
        """Start a new processing step"""
        step_progress = StepProgress(
            step=step,
            progress_percent=0,
            message=message,
            start_time=time.time(),
            details=details or {}
        )
        
        self.status.current_step = step
        self.status.steps.append(step_progress)
        
        # Update overall progress based on step
        step_progress_map = {
            ProcessingStep.INITIALIZING: 5,
            ProcessingStep.PDF_CONVERSION: 20,
            ProcessingStep.PAGE_SEGMENTATION: 50,
            ProcessingStep.ARTICLE_ANALYSIS: 80,
            ProcessingStep.UPLOADING_RESULTS: 95,
            ProcessingStep.COMPLETED: 100,
            ProcessingStep.FAILED: 0
        }
        
        self.status.overall_progress = step_progress_map.get(step, 0)
        self._update_redis()
        
        print(f"ProgressTracker[{self.task_id}]: Started {step.value} - {message}")
    
    def update_step(self, progress_percent: int, message: str = None, details: Dict[str, Any] = None):
        """Update current step progress"""
        if not self.status.steps:
            return
            
        current_step = self.status.steps[-1]
        current_step.progress_percent = progress_percent
        if message:
            current_step.message = message
        if details:
            current_step.details.update(details)
        
        self._update_redis()
    
    def complete_step(self, message: str = None):
        """Complete current processing step"""
        if not self.status.steps:
            return
            
        current_step = self.status.steps[-1]
        current_step.end_time = time.time()
        current_step.duration = current_step.end_time - current_step.start_time
        current_step.progress_percent = 100
        if message:
            current_step.message = message
        
        self._update_redis()
        print(f"ProgressTracker[{self.task_id}]: Completed {current_step.step.value} in {current_step.duration:.2f}s")
    
    def add_page_images(self, images: List[str]):
        """Add converted page images"""
        self.status.total_pages = len(images)
        self.status.images = [{"url": img, "page": i+1, "type": "page"} for i, img in enumerate(images)]
        self._update_redis()
    
    def add_segmentation_result(self, page: int, segmentation_data: Dict[str, Any], article_crops: List[str]):
        """Add segmentation results for a page"""
        segmentation_info = {
            "page": page,
            "timestamp": time.time(),
            "article_count": len(article_crops),
            "crops": [{"url": crop, "type": "article_crop"} for crop in article_crops],
            "segmentation_data": segmentation_data
        }
        
        if self.status.segmentations is None:
            self.status.segmentations = []
        self.status.segmentations.append(segmentation_info)
        
        self.status.processed_pages += 1
        self._update_redis()
    
    def add_article_analysis(self, article_data: Dict[str, Any]):
        """Add analyzed article result"""
        if self.status.articles is None:
            self.status.articles = []
        self.status.articles.append(article_data)
        
        self.status.processed_articles += 1
        self.status.total_articles = len(self.status.articles)
        self._update_redis()
    
    def add_error(self, error_message: str):
        """Add error message"""
        if self.status.errors is None:
            self.status.errors = []
        self.status.errors.append({
            "timestamp": time.time(),
            "message": error_message
        })
        self._update_redis()
        print(f"ProgressTracker[{self.task_id}]: Error - {error_message}")
    
    def set_failed(self, error_message: str):
        """Mark processing as failed"""
        self.add_error(error_message)
        self.status.current_step = ProcessingStep.FAILED
        self.status.overall_progress = 0
        self._update_redis()
    
    def set_completed(self, final_results: Dict[str, Any]):
        """Mark processing as completed"""
        self.status.current_step = ProcessingStep.COMPLETED
        self.status.overall_progress = 100
        
        # Store final results
        if final_results.get("articles"):
            self.status.articles = final_results["articles"]
            self.status.total_articles = len(final_results["articles"])
        
        self._update_redis()
        
        total_duration = time.time() - self.status.start_time
        print(f"ProgressTracker[{self.task_id}]: Completed processing in {total_duration:.2f}s")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current processing status"""
        return asdict(self.status)
    
    def _update_redis(self):
        """Update status in Redis"""
        if not self.redis_client:
            return
            
        try:
            status_data = self.get_status()
            # Convert enum values to strings for JSON serialization
            status_data["current_step"] = status_data["current_step"].value if hasattr(status_data["current_step"], 'value') else str(status_data["current_step"])
            
            for step in status_data["steps"]:
                if hasattr(step["step"], 'value'):
                    step["step"] = step["step"].value
            
            self.redis_client.setex(
                f"task_progress:{self.task_id}",
                3600,  # Expire after 1 hour
                json.dumps(status_data, default=str)
            )
        except Exception as e:
            print(f"ProgressTracker[{self.task_id}]: Redis update failed: {e}")

def get_task_progress(task_id: str, redis_url: str = None) -> Optional[Dict[str, Any]]:
    """Get task progress from Redis"""
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    try:
        redis_client = redis.from_url(redis_url)
        status_data = redis_client.get(f"task_progress:{task_id}")
        
        if status_data:
            return json.loads(status_data)
        return None
    except Exception as e:
        print(f"Error getting task progress for {task_id}: {e}")
        return None