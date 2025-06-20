# --- Final models.py (Clean and Validated) ---
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import datetime

class NodeJsArticleDetailInPayload(BaseModel):
    unique_article_id: Optional[str] = None
    pagenumber: Optional[int] = None
    language: Optional[str] = None
    heading: Optional[str] = None
    content: Optional[str] = None
    english_heading: Optional[str] = None
    english_content: Optional[str] = None
    english_summary: Optional[str] = None
    sentiment: Optional[str] = Field(default="NEUTRAL")
    ministryName: Optional[str] = Field(default="Unknown")
    AdditionMinisrtyName: Optional[List[str]] = Field(default_factory=list)
    image_url: Optional[str] = None
    category: Optional[str] = None
    authors: Optional[List[str]] = Field(default_factory=list)
    originalClipUrls: Optional[List[str]] = Field(default_factory=list)
    processing_error: Optional[str] = Field(default=None, exclude=True)

class NodeJsPayload(BaseModel):
    mediaId: int
    publication: str
    edition: Optional[str] = ""
    zoneName: Optional[str] = ""
    language: str
    date: str
    newsId: Optional[str] = None
    articles: List[Dict[str, Any]]

class DigitalArticleS3JsonContent(BaseModel):
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

class S3DigitalArticleAnalysisTaskInput(BaseModel):
    s3_json_url: str
    request_media_id: int
    request_site_name: Optional[str] = None
    request_timestamp: Optional[str] = None

class PDFProcessingResponse(BaseModel):
    publication: str
    edition: Optional[str] = None
    date: str
    language: str
    total_pages: int
    articles: List[Dict[str, Any]]
    file_urls: Optional[str] = None
