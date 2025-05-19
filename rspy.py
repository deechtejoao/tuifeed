import feedparser
import json
import subprocess
import webbrowser
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import time

MAX_AGE_HOURS = 24  # Adjust as needed
MAX_WORKERS = 5     # Threads for concurrent feed fetching

def fetch_feed(feed):
    """Fetch and process a single feed."""
    name = feed.get('name')
    url = feed.get('url')
    if not name or not url:
        return []

    print(f"Fetching {name}...")

    try:
        parsed_feed = feedparser.parse(url)
    except Exception as e:
        print(f"Error fetching {name}: {e}")
        return []

    if parsed_feed.bozo:
        print(f"Error fetching {name}: {parsed_feed.bozo_exception}")
        return []

    current_time = datetime.now()
    age_limit = timedelta(hours=MAX_AGE_HOURS)
    articles = []

    for entry in parsed_feed.entries:
        # Parse publication time
        if 'published_parsed' not in entry:
            continue
        try:
            published_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
        except Exception:
            continue

        if current_time - published_time > age_limit:
            continue  # Skip old entries

        articles.append({
            'feed': name,
            'title': entry.get('title', 'No title'),
            'link': entry.get('link', 'No link'),
        })

    return articles

def fetch_feeds():
    """Fetch and parse all feeds concurrently."""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return []

    feeds = config.get('feeds', [])
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_feed, feed) for feed in feeds]
        for future in futures:
            try:
                results.extend(future.result())
            except Exception as e:
                print(f"Error in feed fetch thread: {e}")

    return results

def get_fzf_selection(options):
    """Use fzf to select an item from a list."""
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

def main():
    articles = fetch_feeds()
    if not articles:
        print("No articles found.")
        return

    fzf_options = [f"{a['feed']} | {a['title']}" for a in articles]

    while True:
        selected = get_fzf_selection(fzf_options)
        if not selected:
            print("Exiting...")
            break

        for article in articles:
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
