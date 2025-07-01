import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import webbrowser
import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor
from subprocess import Popen, PIPE

# --- Configuration ---
MAX_AGE_HOURS = 24  # Articles older than this won't be shown
MAX_WORKERS = 20
REQUEST_TIMEOUT = 5
RETRY_ATTEMPTS = 2
CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "rss_cache.json"

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class Article:
    """Represents a news article with metadata."""
    def __init__(self, feed: str, title: str, link: str, timestamp: datetime):
        self.feed = feed
        self.title = title
        self.link = link
        self.timestamp = timestamp

    @classmethod
    def from_dict(cls, data: dict) -> 'Article':
        return cls(
            feed=data['feed'],
            title=data['title'],
            link=data['link'],
            timestamp=datetime.fromisoformat(data['timestamp'])
        )

    def to_dict(self) -> dict:
        return {
            'feed': self.feed,
            'title': self.title,
            'link': self.link,
            'timestamp': self.timestamp.isoformat()
        }

    def __str__(self):
        return f"{self.feed} | {self.title}"


# --- Cache Management ---
def ensure_cache_dir() -> None:
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(exist_ok=True)


def read_cache(current_feed_names: Set[str]) -> List[Dict]:
    """Load and filter cached articles based on current feed names."""
    if not CACHE_FILE.exists():
        return []

    try:
        with open(CACHE_FILE, 'r') as f:
            cache_data = json.load(f)

        current_time = datetime.now()
        age_limit = timedelta(hours=MAX_AGE_HOURS)
        
        valid_articles = [
            article for article in cache_data.get("articles", [])
            if current_time - datetime.fromisoformat(article['timestamp']) <= age_limit
            and article['feed'] in current_feed_names
        ]
        
        logger.info(f"Loaded {len(valid_articles)} cached articles.")
        return valid_articles
        
    except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
        logger.error(f"Error reading cache: {e}")
        return []


def write_cache(articles: List[Dict]) -> None:
    """Save articles with timestamp."""
    try:
        ensure_cache_dir()
        cache_data = {
            "timestamp": datetime.now().isoformat(),
            "articles": articles
        }
        
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f, indent=2)
            
    except (OSError, TypeError) as e:
        logger.error(f"Error writing cache: {e}")


# --- Feed Fetching ---
def is_valid_article(entry, age_limit: timedelta) -> bool:
    """Check if article meets requirements."""
    if 'published_parsed' not in entry:
        return False
        
    try:
        published_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
        return datetime.now() - published_time <= age_limit
    except Exception:
        return False


def fetch_feed(feed: Dict) -> List[Dict]:
    """Fetch and parse a single feed with retries."""
    name = feed.get('name')
    url = feed.get('url')
    
    if not name or not url:
        logger.warning(f"Feed missing name or URL: {feed}")
        return []

    logger.info(f"Fetching {name}...")
    
    # Use configured timeout and implement retries
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            headers = {
                'User-Agent': 'RSSBrowser/1.0 (https://github.com/yourname/rssbrowser )'
            }
            response = requests.get(
                url, 
                headers=headers, 
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            parsed_feed = feedparser.parse(response.content)
            break
        except requests.RequestException as e:
            if attempt < RETRY_ATTEMPTS:
                logger.warning(f"Attempt {attempt+1} failed for {name}, retrying...")
                time.sleep(2 * (attempt + 1))
            else:
                logger.error(f"Error fetching {name}: {e}")
                return []
        except Exception as e:
            logger.error(f"Unexpected error fetching {name}: {e}")
            return []

    if parsed_feed.bozo:
        logger.error(f"Error parsing feed {name}: {parsed_feed.bozo_exception}")
        return []

    age_limit = timedelta(hours=MAX_AGE_HOURS)
    
    return [
        {
            'feed': name,
            'title': entry.get('title', 'No title'),
            'link': entry.get('link', 'No link'),
            'timestamp': datetime.fromtimestamp(time.mktime(entry.published_parsed)).isoformat()
        }
        for entry in parsed_feed.entries
        if is_valid_article(entry, age_limit)
    ]


def fetch_feeds(feeds: List[Dict]) -> List[Dict]:
    """Fetch feeds concurrently using provided feed list."""
    logger.info("Fetching feeds concurrently...")
    
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_feed, feed) for feed in feeds]
        
        for future in futures:
            try:
                results.extend(future.result())
            except Exception as e:
                logger.error(f"Error in feed fetch thread: {e}")

    return results


# --- Article Selection ---
def is_fzf_available() -> bool:
    """Check if fzf is available in the system."""
    try:
        with Popen(['fzf', '--version'], stdout=PIPE, stderr=PIPE):
            return True
    except FileNotFoundError:
        return False


def get_fzf_selection(options: List[str]) -> Optional[str]:
    """Use fzf to select an article."""
    if not options:
        logger.info("No articles found.")
        return None
        
    if not is_fzf_available():
        logger.error("fzf not found in system PATH")
        return None

    try:
        fzf = Popen(
            ['fzf', '--prompt=Select Article >'],
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            text=True
        )
        stdout, _ = fzf.communicate(input='\n'.join(options))
        
        return stdout.strip() if fzf.returncode == 0 else None
        
    except Exception as e:
        logger.error(f"Error using fzf: {e}")
        return None


# --- Main Function ---
def load_config() -> Dict:
    """Load configuration from config.json."""
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Config file not found")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config: {e}")
        return {}


def merge_articles(new_articles: List[Dict], cached_articles: List[Dict]) -> List[Dict]:
    """Merge new and cached articles while removing duplicates."""
    seen_links = set()
    merged = []
    
    # Add new articles first to prioritize newer ones
    for article in new_articles + cached_articles:
        if article['link'] not in seen_links and article['link'] != 'No link':
            seen_links.add(article['link'])
            merged.append(article)
            
    return merged


def main():
    """Main application logic."""
    config = load_config()
    feeds = config.get('feeds', [])
    
    if not feeds:
        logger.error("No feeds configured")
        return
    
    current_feed_names = {feed['name'] for feed in feeds if 'name' in feed}
    
    # Read cache with current feeds
    cached_articles = read_cache(current_feed_names)
    
    # Fetch new articles
    new_articles = fetch_feeds(feeds)
    
    # Merge articles and deduplicate
    merged_articles = merge_articles(new_articles, cached_articles)
    
    # Update cache
    if merged_articles:
        write_cache(merged_articles)
    else:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        logger.info("No valid articles to cache")
    
    if not merged_articles:
        logger.info("No articles found.")
        return
    
    # Format for fzf
    fzf_options = [f"{a['feed']} | {a['title']}" for a in merged_articles]
    
    while True:
        selected = get_fzf_selection(fzf_options)
        if not selected:
            logger.info("Exiting...")
            break

        for article in merged_articles:
            if f"{article['feed']} | {article['title']}" == selected:
                link = article['link']
                if link and link != 'No link':
                    logger.info(f"\nOpening in browser: {link}\n")
                    webbrowser.open(link)
                else:
                    logger.warning("\nThis article does not have a valid link.\n")
                break


if __name__ == "__main__":
    main()
