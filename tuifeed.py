import feedparser
import json
import os
import requests
import subprocess
import webbrowser
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import logging
from urllib.parse import urljoin

# --- Configuration ---
MAX_AGE_HOURS = 24  # Articles older than this won't be shown
MAX_WORKERS = 30
REQUEST_TIMEOUT = 5
RETRY_ATTEMPTS = 2
CACHE_DIR = ".cache"
CACHE_FILE = os.path.join(CACHE_DIR, "rss_cache.json")

# --- Logging Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Conditional Request 
def get_feed_etag_cache():
    """Get ETag/Last-Modified cache for conditional requests."""
    etag_file = os.path.join(CACHE_DIR, "feed_etags.json")
    if os.path.exists(etag_file):
        try:
            with open(etag_file, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

# PATCH 3: Improved Feed Fetching with Retry Logic
# Apply this after Patch 2

# REPLACE the existing fetch_feed function with this new one:
def fetch_feed_with_retry(feed, etag_cache, session):
    """Fetch feed with retry logic and conditional requests."""
    name = feed.get('name')
    url = feed.get('url')
    if not name or not url:
        return [], etag_cache

    # Get cached ETag/Last-Modified
    feed_cache_key = f"{name}:{url}"
    cached_headers = etag_cache.get(feed_cache_key, {})
    
    headers = {
        'User-Agent': 'RSSBrowser/1.0',
        'Accept': 'application/rss+xml, application/xml, text/xml',
        'Accept-Encoding': 'gzip, deflate'
    }
    
    # Add conditional request headers
    if 'etag' in cached_headers:
        headers['If-None-Match'] = cached_headers['etag']
    if 'last-modified' in cached_headers:
        headers['If-Modified-Since'] = cached_headers['last-modified']

    for attempt in range(RETRY_ATTEMPTS):
        try:
            logger.info(f"Fetching {name} (attempt {attempt + 1})")
            response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            # Handle 304 Not Modified
            if response.status_code == 304:
                logger.info(f"{name}: Not modified")
                return [], etag_cache
            
            response.raise_for_status()
            
            # Update ETag cache
            new_cache_data = {}
            if 'etag' in response.headers:
                new_cache_data['etag'] = response.headers['etag']
            if 'last-modified' in response.headers:
                new_cache_data['last-modified'] = response.headers['last-modified']
            
            if new_cache_data:
                etag_cache[feed_cache_key] = new_cache_data
            
            break
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Attempt {attempt + 1} failed for {name}: {e}")
            if attempt == RETRY_ATTEMPTS - 1:
                return [], etag_cache
            time.sleep(0.5 * (attempt + 1))  # Exponential backoff

    # Parse feed
    try:
        # Use response.content for better encoding handling
        parsed_feed = feedparser.parse(response.content)
    except Exception as e:
        logger.error(f"Error parsing {name}: {e}")
        return [], etag_cache

    if parsed_feed.bozo and parsed_feed.bozo_exception:
        logger.warning(f"Feed parsing warning for {name}: {parsed_feed.bozo_exception}")

    # Process entries more efficiently
    current_time = datetime.now()
    age_limit = timedelta(hours=MAX_AGE_HOURS)
    new_articles = []

    for entry in parsed_feed.entries:
        # Skip entries without publication date
        published_time = None
        
        # Try multiple date fields
        for date_field in ['published_parsed', 'updated_parsed']:
            if hasattr(entry, date_field) and getattr(entry, date_field):
                try:
                    published_time = datetime.fromtimestamp(time.mktime(getattr(entry, date_field)))
                    break
                except (ValueError, OverflowError):
                    continue
        
        if not published_time:
            continue

        # Age check
        if current_time - published_time > age_limit:
            continue

        # Get article link - handle relative URLs
        link = entry.get('link', '')
        if link and not link.startswith(('http://', 'https://')):
            link = urljoin(url, link)

        new_articles.append({
            'feed': name,
            'title': entry.get('title', 'No title').strip(),
            'link': link,
            'timestamp': published_time.isoformat(),
            'summary': entry.get('summary', '')[:200] + '...' if entry.get('summary') else ''
        })

    logger.info(f"Fetched {len(new_articles)} new articles from {name}")
    return new_articles, etag_cache

def save_feed_etag_cache(etag_cache):
    """Save ETag/Last-Modified cache."""
    etag_file = os.path.join(CACHE_DIR, "feed_etags.json")
    try:
        with open(etag_file, 'w') as f:
            json.dump(etag_cache, f)
    except Exception as e:
        logger.error(f"Error saving ETag cache: {e}")

# --- Cache Management ---

def read_cache(current_feed_names):
    """Load and filter cached articles based on current feed names."""
    if not os.path.exists(CACHE_FILE):
        return []
    try:
        with open(CACHE_FILE, 'r') as f:
            cache_data = json.load(f)

        cached_articles = cache_data.get("articles", [])
        current_time = datetime.now()
        age_limit = timedelta(hours=MAX_AGE_HOURS)

        valid_articles = [
            a for a in cached_articles
            if current_time - datetime.fromisoformat(a['timestamp']) <= age_limit
            and a['feed'] in current_feed_names
        ]
        print(f"Loaded {len(valid_articles)} cached articles.")
        return valid_articles
    except Exception as e:
        print(f"Error reading cache: {e}")
        return []

def write_cache(articles):
    """Save articles with timestamp."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_data = {
        "timestamp": datetime.now().isoformat(),
        "articles": articles
    }
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f, indent=2)
    except Exception as e:
        print(f"Error writing cache: {e}")

# --- Feed Fetching ---

def fetch_feed(feed):
    """Fetch and parse a single feed."""
    name = feed.get('name')
    url = feed.get('url')
    if not name or not url:
        return []

    print(f"Fetching {name}...")

    try:
        headers = {'User-Agent': 'RSSBrowser/1.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        parsed_feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"Error fetching {name}: {e}")
        return []

    if parsed_feed.bozo:
        print(f"Error parsing feed {name}: {parsed_feed.bozo_exception}")
        return []

    current_time = datetime.now()
    age_limit = timedelta(hours=MAX_AGE_HOURS)
    new_articles = []

    for entry in parsed_feed.entries:
        if 'published_parsed' not in entry:
            continue
        try:
            published_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
        except Exception:
            continue

        if current_time - published_time > age_limit:
            continue

        new_articles.append({
            'feed': name,
            'title': entry.get('title', 'No title'),
            'link': entry.get('link', 'No link'),
            'timestamp': published_time.isoformat()
        })

    return new_articles


def fetch_feeds(feeds):
    """Fetch feeds concurrently with optimizations."""
    if not feeds:
        return []

    etag_cache = get_feed_etag_cache()
    all_articles = []
    
    # Use session for connection pooling
    session = requests.Session()
    
    # Configure session
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS,
        max_retries=0  # We handle retries manually
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    logger.info(f"Fetching {len(feeds)} feeds with {MAX_WORKERS} workers...")
    
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            future_to_feed = {
                executor.submit(fetch_feed_with_retry, feed, etag_cache, session): feed 
                for feed in feeds
            }
            
            # Process completed tasks as they finish
            for future in as_completed(future_to_feed):
                try:
                    articles, updated_etag_cache = future.result()
                    all_articles.extend(articles)
                    etag_cache.update(updated_etag_cache)
                except Exception as e:
                    feed = future_to_feed[future]
                    logger.error(f"Error processing feed {feed.get('name', 'unknown')}: {e}")
    
    finally:
        session.close()
        save_feed_etag_cache(etag_cache)

    logger.info(f"Total articles fetched: {len(all_articles)}")
    return all_articles

# --- Article Selection ---

def get_fzf_selection(options):
    """Use fzf to select an article."""
    if not options:
        print("No articles found.")
        return None

    fzf = subprocess.Popen(
        ['fzf', '--prompt=Select Article >'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, _ = fzf.communicate(input='\n'.join(options).encode('utf-8'))
    return stdout.decode('utf-8').strip() if fzf.returncode == 0 else None

# --- Main Function ---

def main():
    # Load config first
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return

    feeds = config.get('feeds', [])
    current_feed_names = {feed['name'] for feed in feeds if 'name' in feed}

    # Read cache with current feeds
    cached_articles = read_cache(current_feed_names)

    # Fetch new articles
    new_articles = fetch_feeds(feeds)

    # Merge new and cached articles
    seen_links = set()
    merged_articles = []

    # Add new articles first to prioritize newer ones
    for a in new_articles:
        if a['link'] not in seen_links and a['link'] != 'No link':
            seen_links.add(a['link'])
            merged_articles.append(a)

    # Add cached articles not already added
    for a in cached_articles:
        if (
            a['link'] not in seen_links
            and a['link'] != 'No link'
            and a['feed'] in current_feed_names
        ):
            seen_links.add(a['link'])
            merged_articles.append(a)

    # Update cache with merged articles
    write_cache(merged_articles)

    # Cleanup
    if merged_articles:
        write_cache(merged_articles)
    else:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        print("No valid articles to cache. Cache file removed.")

    # ... rest of the main function
    if not merged_articles:
        print("No articles found.")
        return

    # Format for fzf
    fzf_options = [f"{a['feed']} | {a['title']}" for a in merged_articles]

    while True:
        selected = get_fzf_selection(fzf_options)
        if not selected:
            print("Exiting...")
            break

        for article in merged_articles:
            if f"{article['feed']} | {article['title']}" == selected:
                link = article['link']
                if link and link != 'No link':
                    print(f"\nOpening in browser: {link}\n")
                    webbrowser.open(link)
                else:
                    print("\nThis article does not have a valid link.\n")
                break

if __name__ == "__main__":
    main()
