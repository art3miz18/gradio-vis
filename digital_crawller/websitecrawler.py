import os
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import boto3
import csv
import time
import random
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import re
from newsplease import NewsPlease
import sys
import json
from newssites import TOP_INDIAN_NEWS_SITES
import threading
from queue import Queue
from direct_gateway_sender import DirectGatewaySender
import multiprocessing
import psutil

s3_client = boto3.client('s3')
S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
NOTIFY_ENDPOINT = os.environ.get("S3_NOTIFY_ENDPOINT")


class IndianNewsCrawler:
    def __init__(self, base_output_dir="digital_data", max_threads=10, use_direct_gateway=True):
        self.base_output_dir = base_output_dir
        # Calculate optimal thread count based on CPU cores
        cpu_count = multiprocessing.cpu_count()
        self.max_threads = min(max_threads, cpu_count * 2)  # 2 threads per CPU core
        self.thread_lock = threading.Lock()
        self.use_direct_gateway = use_direct_gateway
        
        # Initialize the DirectGatewaySender if direct gateway sending is enabled
        if self.use_direct_gateway:
            self.gateway_sender = DirectGatewaySender()
            print(f"DirectGatewaySender initialized for direct sending to gateway")

        # Create base output directory if it doesn't exist
        if not os.path.exists(base_output_dir):
            os.makedirs(base_output_dir)

        # Initialize CSV files with thread-safe file handles
        self._initialize_csv_files()
        
        # Initialize resource monitoring
        self.process = psutil.Process()
        self.memory_threshold = 0.8  # 80% memory usage threshold

        # Headers for requests with rotation capability
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36'
        ]

        # List of top 50 Indian news websites with their language and base URL
        self.top_indian_news_sites = self.load_indian_news_sites()

        # Install required packages
        self.ensure_dependencies()

    def _initialize_csv_files(self):
        """Initialize CSV files with thread-safe file handles"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Master CSV for all articles
        self.master_csv_path = f"{self.base_output_dir}/all_articles_{timestamp}.csv"
        with open(self.master_csv_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['source', 'title', 'url', 'timestamp', 'authors',
                           'date_published', 'content_length', 'language', 'category'])

        # CSV for tracking website crawl stats
        self.stats_csv_path = f"{self.base_output_dir}/crawl_stats_{timestamp}.csv"
        with open(self.stats_csv_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['website', 'articles_found', 'articles_crawled',
                           'successful_articles', 'start_time', 'end_time', 'duration_seconds'])

    def load_indian_news_sites(self):
        """Load the list of top Indian news websites with metadata"""
        # This is a list of top Indian news websites with their language and URL
        return TOP_INDIAN_NEWS_SITES

    def ensure_dependencies(self):
        """Install required dependencies if not already installed"""
        dependencies = [
            ("newspaper3k", "newspaper"),
            ("beautifulsoup4", "bs4"),
            ("requests", "requests"),
            ("news-please", "newsplease"),
            ("indic-nlp-library", "indic_nlp_library")
        ]

        for package, import_name in dependencies:
            try:
                __import__(import_name)
                print(f"{package} is already installed")
            except ImportError:
                print(f"Installing {package}...")
                try:
                    os.system(f"{sys.executable} -m pip install {package}")
                    print(f"{package} installed successfully")
                except Exception as e:
                    print(f"Failed to install {package}: {e}")

    def get_headers(self):
        """Get a random user agent for requests"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def create_site_folder(self, site_name):
        """Create a folder for a specific news site"""
        # Sanitize folder name
        safe_name = ''.join(c if c.isalnum() or c in [
                            ' ', '-', '_'] else '_' for c in site_name)
        folder_path = os.path.join(self.base_output_dir, safe_name)

        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        # Create a site-specific CSV file
        csv_path = os.path.join(folder_path, f"{safe_name}_articles.csv")
        with open(csv_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['title', 'url', 'timestamp', 'authors',
                            'date_published', 'content_length', 'category'])

        return folder_path, csv_path

    def add_to_csv(self, article, site_name, site_csv_path, language="English", category=""):
        """Add article to site-specific CSV file and master CSV file - thread safe"""
        source = site_name
        authors = ', '.join(article.authors) if article.authors else 'Unknown'
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content_length = len(article.maintext) if article.maintext else 0

        # Add to site-specific CSV
        try:
            with self.thread_lock:  # Use lock for thread safety
                with open(site_csv_path, 'a', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        article.title,
                        article.url,
                        timestamp,
                        authors,
                        article.date_publish,
                        content_length,
                        category
                    ])
        except Exception as e:
            print(f"Error adding to site CSV {site_csv_path}: {e}")

        # Add to master CSV
        try:
            with self.thread_lock:  # Use lock for thread safety
                with open(self.master_csv_path, 'a', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        source,
                        article.title,
                        article.url,
                        timestamp,
                        authors,
                        article.date_publish,
                        content_length,
                        language,
                        category
                    ])
        except Exception as e:
            print(f"Error adding to master CSV: {e}")

    def extract_article_content_custom(self, url, site_name):
        """Custom article content extraction when standard methods fail"""
        try:
            print(f"Attempting custom extraction for: {url}")
            response = requests.get(
                url, headers=self.get_headers(), timeout=15)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Save the HTML for debugging if needed
            debug_dir = os.path.join(self.base_output_dir, "debug")
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)

            domain = urlparse(url).netloc.split('.')[0]
            debug_path = f"{debug_dir}/{site_name}_{domain}_debug.html"
            # Only save debug HTML occasionally to save space
            if random.random() < 0.1:  # 10% chance to save debug HTML
                with open(debug_path, 'w', encoding='utf-8') as f:
                    f.write(response.text)

            # Extract title using various methods
            title = None

            # Method 1: Direct h1 tag
            title_element = soup.find('h1')
            if title_element:
                title = title_element.get_text().strip()

            # Method 2: Looking for title in specific classes
            if not title:
                title_candidates = soup.find_all(['h1', 'h2'], class_=lambda c: c and
                                                 any(x in str(c).lower() for x in ['title', 'headline', 'heading']))
                if title_candidates:
                    title = title_candidates[0].get_text().strip()

            # Method 3: Open Graph meta tags
            if not title:
                og_title = soup.find('meta', property='og:title')
                if og_title and og_title.get('content'):
                    title = og_title['content'].strip()

            # Method 4: Twitter card meta tags
            if not title:
                twitter_title = soup.find(
                    'meta', attrs={'name': 'twitter:title'})
                if twitter_title and twitter_title.get('content'):
                    title = twitter_title['content'].strip()

            # Method 5: Regular meta title
            if not title:
                meta_title = soup.find('meta', attrs={'name': 'title'})
                if meta_title and meta_title.get('content'):
                    title = meta_title['content'].strip()

            if not title:
                title = "No title found"

            # Extract content using multiple approaches
            content = ""

            # Approach 1: Article body
            article_body = soup.find('article')
            if article_body:
                paragraphs = article_body.find_all('p')
                if paragraphs:
                    content = "\n\n".join(
                        [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])

            # Approach 2: Look for content div with specific classes for Indian news sites
            if not content:
                content_selectors = [
                    'div.article-body', 'div.story-content', 'div.article__content',
                    'div.entry-content', 'div[itemprop="articleBody"]',
                    'div.story__content', 'div.article-text', '.story-details',
                    'div[data-component="text-block"]', '.article-box', '.news-content',
                    '.main-content', '.story-box', '.news-detail', '.article-container',
                    '.storyDetail', '.articleBody', '.newsText', '.story-article'
                ]

                for selector in content_selectors:
                    try:
                        content_div = soup.select_one(selector)
                        if content_div:
                            paragraphs = content_div.find_all('p')
                            if paragraphs:
                                content = "\n\n".join(
                                    [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
                                if content:
                                    break
                    except Exception:
                        continue

            # Approach 3: Find main div with most paragraphs
            if not content:
                try:
                    main_content = soup.find('main') or soup.find(
                        'div', id=lambda x: x and ('content' in x.lower() or 'article' in x.lower()))
                    if main_content:
                        # Get all divs with multiple paragraphs
                        content_divs = []
                        for div in main_content.find_all('div'):
                            paragraphs = div.find_all('p')
                            if len(paragraphs) >= 3:  # At least 3 paragraphs
                                total_length = sum(
                                    len(p.get_text().strip()) for p in paragraphs)
                                content_divs.append(
                                    (div, len(paragraphs), total_length))

                        # Sort by paragraph count and then by total length
                        content_divs.sort(key=lambda x: (
                            x[1], x[2]), reverse=True)

                        if content_divs:
                            best_div = content_divs[0][0]
                            paragraphs = best_div.find_all('p')
                            content = "\n\n".join(
                                [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
                except Exception:
                    pass

            # Approach 4: Just collect all paragraphs with substantial text
            if not content:
                try:
                    all_paragraphs = soup.find_all('p')
                    content_paras = [p.get_text().strip() for p in all_paragraphs
                                     if len(p.get_text().strip()) > 40 and not any(x in p.get_text().lower()
                                                                                   for x in ['cookie', 'subscribe', 'newsletter', 'sign up', 'advertisement'])]
                    if content_paras:
                        content = "\n\n".join(content_paras)
                except Exception:
                    pass

            # Extract author information
            authors = []
            try:
                # Method 1: Look for author meta tags
                meta_author = soup.find('meta', attrs={'name': 'author'})
                if meta_author and meta_author.get('content'):
                    authors.append(meta_author['content'].strip())

                # Method 2: Look for author elements
                if not authors:
                    author_elements = soup.find_all(
                        ['span', 'div', 'a'], class_=lambda c: c and 'author' in str(c).lower())
                    for element in author_elements:
                        author_text = element.get_text().strip()
                        if 3 < len(author_text) < 50 and author_text.lower() not in ['author', 'by', 'written by']:
                            # Clean up the author text
                            author_text = re.sub(
                                r'^by\s+', '', author_text, flags=re.IGNORECASE).strip()
                            if author_text and author_text not in authors:
                                authors.append(author_text)

                # Method 3: Look for byline
                if not authors:
                    byline = soup.find(
                        class_=lambda c: c and 'byline' in str(c).lower())
                    if byline:
                        author_text = byline.get_text().strip()
                        author_text = re.sub(
                            r'^by\s+', '', author_text, flags=re.IGNORECASE).strip()
                        if author_text:
                            authors.append(author_text)
            except Exception:
                pass

            # Extract date
            date_publish = None
            try:
                # Method 1: Look for date meta tags
                for meta_name in ['published_time', 'article:published_time', 'date', 'pubdate']:
                    meta_date = soup.find('meta', attrs={'name': meta_name}) or soup.find(
                        'meta', property=meta_name)
                    if meta_date and meta_date.get('content'):
                        date_publish = meta_date['content'].strip()
                        break

                # Method 2: Look for time elements
                if not date_publish:
                    time_element = soup.find('time')
                    if time_element and time_element.get('datetime'):
                        date_publish = time_element['datetime'].strip()
                    elif time_element:
                        date_publish = time_element.get_text().strip()

                # Method 3: Look for date in URL
                if not date_publish:
                    date_patterns = [
                        # yyyy-mm-dd or yyyy/mm/dd
                        r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',
                        # dd-mm-yyyy or dd/mm/yyyy
                        r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})',
                    ]

                    for pattern in date_patterns:
                        match = re.search(pattern, url)
                        if match:
                            date_publish = '-'.join(match.groups())
                            break
            except Exception:
                pass

            if not date_publish:
                date_publish = datetime.now().strftime("%Y-%m-%d")

            # Extract category if possible
            category = ""
            try:
                # Method 1: From URL structure
                path_parts = urlparse(url).path.strip('/').split('/')
                if len(path_parts) > 0:
                    potential_category = path_parts[0].replace(
                        '-', ' ').title()
                    # Filter out non-category paths
                    if potential_category not in ['Article', 'Story', 'News'] and len(potential_category) > 2:
                        category = potential_category

                # Method 2: From meta tags
                if not category:
                    meta_category = soup.find('meta', attrs={'name': 'category'}) or soup.find(
                        'meta', property='article:section')
                    if meta_category and meta_category.get('content'):
                        category = meta_category['content'].strip()
            except Exception:
                pass

            # Create pseudo article object compatible with our existing functions
            class CustomArticle:
                def __init__(self, url, title, content, authors, date_publish, category):
                    self.url = url
                    self.title = title
                    self.maintext = content
                    self.source_domain = urlparse(url).netloc
                    self.authors = authors
                    self.date_publish = date_publish
                    self.category = category

            # Only return if we have both title and content

            # Ensure we have substantial content
            if title and content and len(content) >= 500:
                return CustomArticle(url, title, content, authors, date_publish, category)
            else:

                return None

        except Exception as e:
            print(f"Custom extraction failed for {url}: {e}")
            return None

    def process_article(self, url, site_name, site_folder, site_csv_path, language):
        """Process a single article URL - modified to only save current day's articles with sufficient content"""
        print(f"Processing {site_name} article: {url}")
        try:
            # Get the current date in YYYY-MM-DD format
            current_date = datetime.now().strftime("%Y-%m-%d")
            
            # Try different extraction methods, beginning with NewsPlease
            article = None
            
            # Method 1: Try with NewsPlease first
            try:
                article = NewsPlease.from_url(url)
                if article and article.maintext:
                    # Check content length requirement (min 500 characters)
                    if len(article.maintext.strip()) < 500:
                        print(f"Skipping article - content too short ({len(article.maintext.strip())} chars): {url}")
                        return False
                        
                    # Check if the article was published today
                    if not self.is_current_day_article(article.date_publish, current_date):
                        print(f"Skipping article - not from current day: {url}")
                        return False
                        
                    # Success with NewsPlease
                    return self.save_article_if_not_duplicate(article, site_name, site_folder, site_csv_path, language)
            except Exception as e:
                print(f"NewsPlease failed for {url}: {e}")
            
            # Method 2: Try with newspaper3k
            try:
                from newspaper import Article as NewsArticle
                from newspaper import Config
                
                config = Config()
                config.browser_user_agent = random.choice(self.user_agents)
                config.request_timeout = 20
                config.memoize_articles = False
                
                news_article = NewsArticle(url, config=config)
                news_article.download()
                news_article.parse()
                
                if news_article.text:
                    # Check content length requirement (min 500 characters)
                    if len(news_article.text.strip()) < 500:
                        print(f"Skipping article - content too short ({len(news_article.text.strip())} chars): {url}")
                        return False
                        
                    # Convert newspaper3k article to format similar to NewsPlease
                    class ArticleAdapter:
                        def __init__(self, news_article, url):
                            self.title = news_article.title
                            self.url = url
                            self.source_domain = urlparse(url).netloc
                            self.date_publish = news_article.publish_date
                            self.authors = news_article.authors
                            self.maintext = news_article.text
                            
                    adapted_article = ArticleAdapter(news_article, url)
                    
                    # Check if the article was published today
                    if not self.is_current_day_article(adapted_article.date_publish, current_date):
                        print(f"Skipping article - not from current day: {url}")
                        return False
                        
                    return self.save_article_if_not_duplicate(adapted_article, site_name, site_folder, site_csv_path, language)
            except Exception as e:
                print(f"newspaper3k failed for {url}: {e}")
            
            # Method 3: Try custom extraction as last resort
            custom_article = self.extract_article_content_custom(url, site_name)
            if custom_article and custom_article.maintext:
                # Check content length requirement (min 500 characters)
                if len(custom_article.maintext.strip()) < 500:
                    print(f"Skipping article - content too short ({len(custom_article.maintext.strip())} chars): {url}")
                    return False
                    
                # Check if the article was published today
                if not self.is_current_day_article(custom_article.date_publish, current_date):
                    print(f"Skipping article - not from current day: {url}")
                    return False
                    
                return self.save_article_if_not_duplicate(custom_article, site_name, site_folder, site_csv_path, language)
            
            print(f"Failed to extract content from {url} with all methods")
            return False
            
        except Exception as e:
            print(f"Error processing {url}: {e}")
            return False

    def extract_category_from_url(self, url):
        """Extract potential news category from URL"""
        path = urlparse(url).path.strip('/')
        if not path:
            return ""

        # Common categories in news URLs
        categories = ['politics', 'business', 'economy', 'markets', 'health',
                      'science', 'tech', 'technology', 'sports', 'entertainment',
                      'culture', 'lifestyle', 'opinion', 'world', 'national',
                      'education', 'environment', 'crime', 'city', 'india']

        path_parts = path.split('/')
        for part in path_parts:
            if part.lower() in categories:
                return part.capitalize()

        # If we couldn't find a match in our predefined categories,
        # use the first path component if it looks reasonable
        if path_parts and 3 < len(path_parts[0]) < 20:
            return path_parts[0].replace('-', ' ').capitalize()

        return ""

    def is_likely_article(self, url):
        """Enhanced check if a URL is likely to be a news article"""
        # Ignore URLs that end with these extensions
        ignored_extensions = ['.pdf', '.jpg', '.jpeg',
                              '.png', '.gif', '.mp4', '.mp3', '.zip']
        if any(url.lower().endswith(ext) for ext in ignored_extensions):
            return False

        # Check URL structure
        url_path = urlparse(url).path.lower()

        # Skip if URL has no path or is just the homepage
        if not url_path or url_path == "/":
            return False

        # Don't skip section pages that might be article listings from major publications
        # But do skip administrative pages and non-article content
        skip_patterns = [
            '/tag/',
            '/author/',
            '/login/',
            '/subscribe/',
            '/contact/',
            '/about/',
            '/terms/',
            '/privacy/',
            '/advertise/',
            '/search/',
            '/rss/',
            '/photo/',
            '/image/',
            '/gallery/'
        ]

        # Only skip category pages if they seem to be administrative rather than content
        # Handle different URL patterns from major Indian news sites
        common_content_sections = [
            '/briefs', '/india', '/world', '/politics', '/business',
            '/sports', '/entertainment', '/tech', '/science', '/health',
            '/lifestyle', '/city', '/education', '/astrology', '/opinion', '/brief'
        ]

        # Check if the URL looks like a content section from a major publication
        is_content_section = False
        for section in common_content_sections:
            if section in url_path:
                is_content_section = True
                break

        # Skip if matches skip patterns AND is not a content section
        if any(pattern in url_path for pattern in skip_patterns) and not is_content_section:
            return False

        # Still skip video pages and pure listing pages unless they're from specific known formats
        pure_listing_patterns = [
            '/articlelist/',
            '/videoshow/',
            '/videos/',
            '/photostory/',
            '/list/'
        ]
        if any(pattern in url_path for pattern in pure_listing_patterns) and not is_content_section:
            return False

        # Comprehensive pattern matching for article indicators
        patterns = [
            # Standard article indicators
            '/article/', '/story/', '/news/', '/post/',
            # Year patterns
            '/202[0-9]/', '/2025/', '/2024/', '/2023/', '/2022/', '/2021/', '/2020/',
            # Content categories common in Indian news
            '/politics/', '/business/', '/economy/', '/markets/',
            '/health/', '/science/', '/tech/', '/technology/',
            '/sports/', '/entertainment/', '/culture/', '/lifestyle/',
            '/opinion/', '/editorial/', '/world/', '/national/', '/local/',
            '/crime/', '/education/', '/environment/', '/analysis/',
            '/india/', '/bharat/', '/desh/', '/video-news/',
            '/state/', '/city/', '/movies/', '/tv/', '/trending/', '/briefs', '/brief'
        ]

        # Check for common article patterns
        if any(pattern in url_path for pattern in patterns):
            return True

        # Check for date patterns in URL (common in news articles)
        date_patterns = [
            r'/\d{4}/\d{1,2}/\d{1,2}/',  # /yyyy/mm/dd/
            r'/\d{4}-\d{1,2}-\d{1,2}/',  # /yyyy-mm-dd/
            r'/\d{1,2}-\d{1,2}-\d{4}/',  # /dd-mm-yyyy/
            r'/\d{4}/\d{1,2}/',          # /yyyy/mm/
        ]

        if any(re.search(pattern, url_path) for pattern in date_patterns):
            return True

        # Check for ID patterns (common in some Indian news sites)
        id_patterns = [
            r'/news-\d+', r'/article-\d+', r'/story-\d+',
            r'-\d+\.html', r'/\d+\.html', r'_\d+\.html'
        ]

        if any(re.search(pattern, url_path) for pattern in id_patterns):
            # NEW: Make sure the URL doesn't end with just a number (likely a category)
            path_parts = url_path.strip('/').split('/')
            if path_parts and not path_parts[-1].isdigit():
                return True

        # Check for query parameters that often indicate articles
        query = urlparse(url).query
        if query and any(param in query for param in ['articleid', 'storyid', 'newsid', 'id=']):
            return True

        # NEW: Check for minimum path depth for likely article
        # Most article URLs have at least 3 path components
        path_parts = url_path.strip('/').split('/')
        if len(path_parts) >= 3 and len(path_parts[-1]) > 5:
            return True

        return False

    def extract_article_links(self, website_url, site_name, min_articles=30, max_pages=10):
        """Extract news article links from an Indian news website with focus on latest news"""
        article_urls = set()
        base_domain = urlparse(website_url).netloc
        pages_to_visit = [website_url]
        visited_pages = set()

        # First prioritize latest news sections if they exist
        latest_news_urls = []
        for latest_section in ['latest', 'latest-news', 'breaking-news', 'recent', 'today', 'top-news', 'trending']:
            latest_url = urljoin(website_url, latest_section)
            latest_news_urls.append(latest_url)

        # Add these to the front of the queue
        pages_to_visit = latest_news_urls + pages_to_visit

        # NEW: Add direct URL patterns that are likely to contain article lists
        # This is especially useful for sites that don't have standard "latest" sections
        common_article_sections = [
            'news', 'india', 'world', 'politics', 'business', 'sports',
            'entertainment', 'technology', 'science', 'health', 'lifestyle'
        ]

        for section in common_article_sections:
            section_url = urljoin(website_url, section)
            if section_url not in pages_to_visit:
                pages_to_visit.append(section_url)

        page_count = 0

        # NEW: Track article candidates separately from filtered articles
        article_candidates = set()

        while pages_to_visit and page_count < max_pages and len(article_urls) < min_articles:
            current_url = pages_to_visit.pop(0)

            if current_url in visited_pages:
                continue

            visited_pages.add(current_url)
            page_count += 1

            print(
                f"[{site_name}] Scanning page {page_count}/{max_pages}: {current_url}")

            try:
                response = requests.get(
                    current_url, headers=self.get_headers(), timeout=15)
                if response.status_code != 200:
                    print(
                        f"Failed to fetch {current_url}, status code: {response.status_code}")
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')

                # Method 1: Find headline containers (common in Indian news sites)
                headline_containers = soup.select(
                    'div.headline, div.news-item, div.article-box, .news-card, .story-card, .article-item')
                for container in headline_containers:
                    link = container.find('a', href=True)
                    if link and link['href']:
                        full_url = urljoin(current_url, link['href'])
                        article_candidates.add(full_url)

                # Method 2: Find all links inside headings
                headlines = soup.find_all(['h1', 'h2', 'h3', 'h4'])
                for headline in headlines:
                    links = headline.find_all('a', href=True)
                    for link in links:
                        full_url = urljoin(current_url, link['href'])
                        article_candidates.add(full_url)

                # Method 3: Find all links and filter for likely articles
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Skip empty, javascript, and anchor links
                    if not href or href.startswith(('javascript:', '#')):
                        continue

                    full_url = urljoin(current_url, href)

                    # Only consider URLs from the same domain
                    if urlparse(full_url).netloc != base_domain:
                        continue

                    # Skip URLs with common non-article elements
                    if any(skip in full_url.lower() for skip in [
                        'login', 'sign-in', 'subscribe', 'contact', 'about',
                        'advertise', 'terms', 'privacy', 'sitemap', 'search',
                        'tag/', 'author/', 'photo/', 'image/',
                        'javascript:', 'mailto:', 'tel:', 'print/', 'rss/'
                    ]):
                        continue

                    article_candidates.add(full_url)

                # NEW: Apply strict filtering to candidate URLs
                for url in article_candidates:
                    if url not in article_urls and self.is_likely_article(url):
                        article_urls.add(url)
                    # Otherwise, consider it for further crawling if we haven't visited it yet
                    elif url not in visited_pages and url not in pages_to_visit:
                        # Only add category pages for further crawling
                        path = urlparse(url).path
                        if path and '/' in path and path.count('/') <= 2:
                            pages_to_visit.append(url)

                # Be polite to the server
                delay = random.uniform(1, 3)
                print(
                    f"[{site_name}] Found {len(article_urls)} potential articles so far. Waiting {delay:.2f} seconds...")
                time.sleep(delay)

            except Exception as e:
                print(f"Error scanning {current_url}: {str(e)}")

        article_url_list = list(article_urls)
        print(f"[{site_name}] Found {len(article_url_list)} potential article URLs")

        return article_url_list

    def crawl_site(self, site_info, min_articles=30):
        """Crawl a single news site for latest articles using multithreading"""
        site_name = site_info["name"]
        site_url = site_info["url"]
        language = site_info["language"]

        print(f"\n{'='*67}")
        print(f"Starting crawl for {site_name} ({language})")
        print(f"{'='*67}")

        # Create site-specific folder and CSV
        site_folder, site_csv_path = self.create_site_folder(site_name)

        # Track crawl statistics
        start_time = datetime.now()
        articles_found = 0
        articles_crawled = 0
        successful_articles = 0

        try:
            # Extract article links
            article_urls = self.extract_article_links(
                site_url, site_name, min_articles=min_articles, max_pages=80)
            articles_found = len(article_urls)

            # NEW: If we found very few articles, try a fallback approach
            if articles_found < 5:
                print(
                    f"[{site_name}] Initial crawl found too few articles. Trying fallback approach...")
                # Your existing fallback approach code...

            # Use ThreadPoolExecutor for parallel article processing
            with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
                # Submit tasks to the thread pool
                future_to_url = {
                    executor.submit(self.process_article, url, site_name, site_folder, site_csv_path, language): url
                    for url in article_urls[:min_articles]
                }

                # Process results as they complete
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    articles_crawled += 1

                    try:
                        success = future.result()
                        if success:
                            with self.thread_lock:
                                successful_articles += 1

                            # Check if we have enough articles
                            if successful_articles >= min_articles:
                                # Cancel remaining futures if possible
                                for f in future_to_url:
                                    if not f.done():
                                        f.cancel()
                                break

                    except Exception as e:
                        print(f"Error processing {url}: {str(e)}")

        except Exception as e:
            print(f"Error crawling {site_name}: {str(e)}")

        # Record end time and crawl stats
        end_time = datetime.now()
        duration_seconds = (end_time - start_time).total_seconds()

        # Add stats to CSV - use lock for thread safety
        with self.thread_lock:
            with open(self.stats_csv_path, 'a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow([
                    site_name,
                    articles_found,
                    articles_crawled,
                    successful_articles,
                    start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    duration_seconds
                ])

        print(f"\nCrawl completed for {site_name}")
        print(f"Articles found: {articles_found}")
        print(f"Articles processed: {articles_crawled}")
        print(f"Successful articles: {successful_articles}")
        print(f"Duration: {duration_seconds:.2f} seconds")

        return successful_articles

    def check_resources(self):
        """Check if system resources are within acceptable limits"""
        memory_percent = psutil.virtual_memory().percent / 100
        if memory_percent > self.memory_threshold:
            print(f"Warning: High memory usage ({memory_percent:.1%})")
            return False
        return True

    def crawl_all_sites(self, min_articles_per_site=200, max_sites=67, parallel_sites=3):
        """Crawl all top Indian news sites with improved parallel processing"""
        print(f"Starting crawl of top {max_sites} Indian news sites")
        print(f"Targeting {min_articles_per_site} articles per site")
        print(f"Processing up to {parallel_sites} sites in parallel")
        print(f"Using {self.max_threads} threads per site")
        
        # Retry any pending uploads first if using direct gateway
        if self.use_direct_gateway:
            self.retry_pending_uploads()

        # Track overall statistics
        start_time = datetime.now()
        total_sites_crawled = 0
        total_articles_crawled = 0

        # Filter out already crawled sites
        sites_to_crawl = []
        for site_info in self.top_indian_news_sites[:max_sites]:
            site_name = site_info["name"]
            site_folder = os.path.join(
                self.base_output_dir, site_name.replace(' ', '_'))

            if os.path.exists(site_folder):
                files = os.listdir(site_folder)
                if len([f for f in files if f.endswith('.txt')]) >= min_articles_per_site:
                    print(f"Skipping {site_name} - already has enough articles")
                    continue

            sites_to_crawl.append(site_info)

        # Process sites sequentially instead of using ProcessPoolExecutor
        # This avoids the pickling error with thread locks
        for site_info in sites_to_crawl:
            site_name = site_info["name"]
            
            try:
                articles_crawled = self.crawl_site(site_info, min_articles_per_site)
                total_sites_crawled += 1
                total_articles_crawled += articles_crawled

                print(f"Completed crawling {site_name}. Progress: {total_sites_crawled}/{len(sites_to_crawl)} sites")
                
                # Check system resources after each site
                if not self.check_resources():
                    print("System resources low, pausing for 30 seconds...")
                    time.sleep(30)

            except Exception as e:
                print(f"Error crawling site {site_name}: {str(e)}")

        # Record overall statistics
        end_time = datetime.now()
        duration = end_time - start_time

        print(f"\n{'='*67}")
        print(f"Crawl completed for {total_sites_crawled} sites")
        print(f"Total articles crawled: {total_articles_crawled}")
        print(f"Duration: {duration}")
        
        # Check for any pending uploads after crawling if using direct gateway
        if self.use_direct_gateway:
            self.retry_pending_uploads()
            
        return total_articles_crawled

    def retry_pending_uploads(self):
        """Check for any pending uploads that failed previously"""
        if not self.use_direct_gateway:
            print("Direct gateway sending is disabled. Skipping check of pending uploads.")
            return
        
        print("Checking for pending uploads...")
        start_time = datetime.now()
        result = {'failure': 0}
        try:
            result = self.gateway_sender.retry_failed_requests()
            print(f"Found {result['failure']} failed requests")
        except Exception as e:
            print(f"Error checking pending uploads: {e}")
        
        # Calculate duration
        end_time = datetime.now()
        duration = end_time - start_time
        duration_seconds = duration.total_seconds()
        
        print(f"Total check duration: {duration_seconds:.2f} seconds requests")
        print(f"Data directory: {self.base_output_dir}")
        
        # Only print CSV paths if they exist as attributes
        if hasattr(self, 'master_csv_path'):
            print(f"Master CSV: {self.master_csv_path}")
        if hasattr(self, 'stats_csv_path'):
            print(f"Stats CSV: {self.stats_csv_path}")
            
        print(f"{'='*67}")

        # Return the number of failed requests
        return result.get('failure', 0)

    def detect_language(self, text):
        """Detect the language of text (basic implementation)"""
        # A more sophisticated implementation could use langdetect or similar libraries
        # This is a basic approach for common Indian languages

        # Get first 200 characters for analysis
        sample = text[:200].lower()

        # Check for Devanagari script (Hindi and other languages)
        if any('\u0900' <= char <= '\u097f' for char in sample):
            return "Hindi"

        # Check for Bengali script
        if any('\u0980' <= char <= '\u09ff' for char in sample):
            return "Bengali"

        # Check for Tamil script
        if any('\u0b80' <= char <= '\u0bff' for char in sample):
            return "Tamil"

        # Check for Malayalam script
        if any('\u0d00' <= char <= '\u0d7f' for char in sample):
            return "Malayalam"

        # Check for Telugu script
        if any('\u0c00' <= char <= '\u0c7f' for char in sample):
            return "Telugu"

        # Check for Kannada script
        if any('\u0c80' <= char <= '\u0cff' for char in sample):
            return "Kannada"

        # Check for Gujarati script
        if any('\u0a80' <= char <= '\u0aff' for char in sample):
            return "Gujarati"

        # Check for Odia script
        if any('\u0b00' <= char <= '\u0b7f' for char in sample):
            return "Odia"

        # Check for Punjabi (Gurmukhi) script
        if any('\u0a00' <= char <= '\u0a7f' for char in sample):
            return "Punjabi"

        # Check for Marathi (uses Devanagari script but may have specific words)
        if "marathi" in sample:
            return "Marathi"

        # Default to English
        return "English"

    def save_article(self, article, site_name, site_folder="output", language="English"):
        """Save article as JSON to S3 or send directly to gateway"""

        title = article.title if article.title else "No_Title"
        safe_title = "".join(c if c.isalnum() else "_" for c in title)[:50]
        date_folder = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{site_name}_{safe_title}_{timestamp}.json"

        # Prepare article JSON data
        article_json = {
            "title": article.title,
            "source": site_name,
            "url": article.url,
            "date_published": article.date_publish.isoformat() if hasattr(article.date_publish, "isoformat") else str(article.date_publish),
            "authors": article.authors,
            "language": language,
            "crawled_on": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "content": article.maintext if article.maintext else "No content extracted",
            "category": getattr(article, 'category', ''),
            "imagesUrls": getattr(article, 'image_url', []) if isinstance(getattr(article, 'image_url', []), list) else [getattr(article, 'image_url', '')] if getattr(article, 'image_url', '') else [],
            "originalClipUrls": [article.url] if article.url else [],
            "mediaId": 2  # Default media ID for digital news
        }

        # Check if we should use direct gateway sending
        if self.use_direct_gateway:
            try:
                print(f"🚀 Sending article directly to gateway: {title}")
                result = self.gateway_sender.send_article_to_gateway(article_json)
                
                if result["status"] == "success":
                    print(f"✅ Article sent successfully to gateway: {title}")
                    print(f"Task ID: {result.get('task_id', 'N/A')}")
                    return result.get('task_id', 'direct_sent')
                else:
                    print(f"❌ Failed to send article to gateway: {title}")
                    print(f"Error: {result.get('message', 'Unknown error')}")
                    
                    # Fall back to S3 upload if direct sending fails
                    print(f"Falling back to S3 upload for {title}")
            except Exception as e:
                print(f"❌ Error sending article directly to gateway: {e}")
                print(f"Falling back to S3 upload for {title}")
        
        # If direct gateway sending is disabled or failed, upload to S3
        try:
            s3_key = f"Gyaandeep_webcrawler/digital/websites/{site_name}/{safe_title}/{date_folder}/{filename}"
            
            # Upload to S3
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=json.dumps(article_json, ensure_ascii=False),
                ContentType='application/json'
            )
            s3_url = f"s3://{S3_BUCKET}/{s3_key}"
            print(f"✅ Uploaded to S3: {s3_url}")

            # Notify another service using the S3 URL
            if NOTIFY_ENDPOINT:
                payload = {
                    "s3_url": s3_url,
                    "site_name": site_name,
                    "title": article.title,
                    "timestamp": timestamp,
                    "mediaId": 2
                }

                try:
                    response = requests.post(
                        NOTIFY_ENDPOINT, json=payload, timeout=10)
                    if response.status_code == 202:
                        print(f"✅ Notified endpoint: {NOTIFY_ENDPOINT}")
                    else:
                        print(
                            f"❌ Failed to notify service. Status: {response.status_code}, Response: {response.text}")
                except Exception as e:
                    print(f"❌ Error posting to notify service: {e}")

            return s3_url
            return s3_url, len(article_json["content"])

        except Exception as e:
            print(f"Error uploading to S3: {e}")
            return None, 0

    def is_current_day_article(self, article_date, current_date):
        """Check if the article is from the current day with improved date parsing"""
        if not article_date:
            return False
        
        try:
            # Try to parse the date string
            if isinstance(article_date, str):
                # Try multiple date formats
                date_formats = [
                    "%Y-%m-%d",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%d-%m-%Y",
                    "%d/%m/%Y",
                    "%Y/%m/%d",
                    "%B %d, %Y",
                    "%d %B %Y",
                    "%b %d, %Y",
                    "%d %b %Y"
                ]
                
                parsed_date = None
                for date_format in date_formats:
                    try:
                        parsed_date = datetime.strptime(article_date.split('T')[0], date_format)
                        break
                    except ValueError:
                        continue
                    
                if not parsed_date:
                    return False
            else:
                parsed_date = article_date
            
            # Convert to datetime if it's a date object
            if isinstance(parsed_date, datetime):
                parsed_date = parsed_date.date()
            
            # Compare with current date
            return parsed_date == datetime.strptime(current_date, "%Y-%m-%d").date()
        
        except Exception as e:
            print(f"Error parsing date {article_date}: {e}")
            return False

    def save_article_if_not_duplicate(self, article, site_name, site_folder, site_csv_path, language):
        """Check for duplicates before saving article, and handle accordingly"""
        # Get a list of existing JSON files in the folder
        existing_files = [f for f in os.listdir(
            site_folder) if f.endswith('.json')]

        # Check for duplicates based on headline
        duplicate_found = False
        duplicate_files = []

        for filename in existing_files:
            try:
                with open(os.path.join(site_folder, filename), 'r', encoding='utf-8') as f:
                    existing_article = json.load(f)

                    # Compare titles (case insensitive)
                    if existing_article.get('title', '').lower() == article.title.lower():
                        duplicate_found = True
                        duplicate_files.append((filename, existing_article))
            except Exception as e:
                print(f"Error reading existing file {filename}: {e}")

        if duplicate_found:
            print(f"Duplicate article found: {article.title}")

            # Keep the latest one based on date_published
            latest_file = None
            latest_date = None

            # Include the current article in the comparison
            article_json = {
                "title": article.title,
                "source": site_name,
                "sourceUrls": article.url,
                "date_published": article.date_publish.isoformat() if hasattr(article.date_publish, "isoformat") else str(article.date_publish),
                "authors": article.authors if article.authors else ["Unknown"],
                "language": language,
                "crawled_on": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "content": article.maintext if article.maintext else "No content extracted",
                "category": getattr(article, 'category', '')
            }

            current_date = self.parse_date_published(
                article_json.get('date_published', ''))

            if current_date:
                latest_date = current_date
                latest_file = None  # None indicates it's the current article

            # Compare with existing duplicates
            for filename, existing in duplicate_files:
                existing_date = self.parse_date_published(
                    existing.get('date_published', ''))

                if existing_date and (not latest_date or existing_date > latest_date):
                    latest_date = existing_date
                    latest_file = filename

            # Delete all duplicates
            for filename, _ in duplicate_files:
                try:
                    os.remove(os.path.join(site_folder, filename))
                    print(f"Deleted duplicate: {filename}")
                except Exception as e:
                    print(f"Error deleting duplicate file {filename}: {e}")

            # If the current article is the latest, save it
            if latest_file is None:
                print(f"Current article is the latest version, saving it.")
                return self.save_article(article, site_name, site_folder, language)
            else:
                print(f"Existing article {latest_file} is newer, keeping it.")
                return False
        else:
            # No duplicate found, save the article
            return self.save_article(article, site_name, site_folder, language)

    def parse_date_published(self, date_str):
        """Parse a date string into a datetime object for comparison"""
        if not date_str:
            return None

        try:
            # Handle ISO format (common in our JSON data)
            if 'T' in date_str:
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            # Handle other potential formats
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d-%m-%Y %H:%M:%S', '%d-%m-%Y']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            return None
        except Exception:
            return None

    def get_site_categories(self, site_url, site_name):
        """Get main categories from a news site for better article discovery"""
        categories = []

        try:
            response = requests.get(
                site_url, headers=self.get_headers(), timeout=15)
            if response.status_code != 200:
                return categories

            soup = BeautifulSoup(response.text, 'html.parser')

            # Look for category links in the navigation
            nav_elements = soup.select('nav, .nav, .navigation, .menu, header')

            for nav in nav_elements:
                links = nav.find_all('a', href=True)
                for link in links:
                    url = urljoin(site_url, link['href'])

                    # Skip non-domain links and obvious non-category links
                    if urlparse(url).netloc != urlparse(site_url).netloc:
                        continue

                    if any(skip in url.lower() for skip in ['login', 'subscribe', 'contact', 'about']):
                        continue

                    # Get the text and clean it
                    text = link.get_text().strip()
                    if 3 < len(text) < 20:  # Reasonable length for a category name
                        categories.append({
                            'name': text,
                            'url': url
                        })

            # Limit to reasonable number and remove duplicates
            unique_categories = []
            unique_urls = set()

            for category in categories:
                if category['url'] not in unique_urls:
                    unique_urls.add(category['url'])
                    unique_categories.append(category)

            return unique_categories[:10]  # Limit to top 10 categories

        except Exception as e:
            print(f"Error getting categories for {site_name}: {str(e)}")
            return categories


if __name__ == "__main__":
    # Set default values instead of prompting for input
    output_dir = "digital_data"
    min_articles = 250
    max_sites = 67
    max_threads = 10        # Threads per site for article processing
    parallel_sites = 6     # Number of sites to process in parallel

    # Create and run the crawler
    crawler = IndianNewsCrawler(
        base_output_dir=output_dir, max_threads=max_threads)

    print(f"\nStarting crawl of up to {max_sites} Indian news sites")
    print(f"Targeting minimum {min_articles} articles per site")
    print(
        f"Using {max_threads} threads per site and processing {parallel_sites} sites in parallel")
    print(f"Output will be saved to: {output_dir}")

    try:
        total_articles = crawler.crawl_all_sites(
            min_articles_per_site=min_articles,
            max_sites=max_sites,
            parallel_sites=parallel_sites)
        print(f"\nCrawling completed successfully!")
        print(f"Total articles crawled: {total_articles}")

    except KeyboardInterrupt:
        print("\nCrawling aborted by user.")
        print("Partial results have been saved.")
    except Exception as e:
        print(f"\nAn error occurred during crawling: {str(e)}")
        print("Partial results may have been saved.")
