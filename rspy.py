import feedparser
import json
import subprocess
import webbrowser
import time
from datetime import datetime, timedelta

def fetch_feeds():
    """Fetch and parse all feeds from config.json, filtering by age."""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found.")
        return []
    except json.JSONDecodeError:
        print("Error: config.json is not valid JSON.")
        return []

    MAX_AGE_HOURS = 48  # Extend this as needed
    current_time = datetime.now()
    age_limit = timedelta(hours=MAX_AGE_HOURS)

    articles = []
    for feed in config.get('feeds', []):
        name = feed.get('name')
        url = feed.get('url')
        if not name or not url:
            continue

        print(f"Fetching {name}...")

        parsed_feed = feedparser.parse(url)
        if parsed_feed.bozo:
            print(f"Error fetching {name}: {parsed_feed.bozo_exception}")
            continue

        for entry in parsed_feed.entries:
            # Attempt to parse published time
            published_time = None
            if 'published_parsed' in entry:
                try:
                    published_time = datetime.fromtimestamp(
                        time.mktime(entry.published_parsed)
                    )
                except Exception as e:
                    print(f"Error parsing date for {entry.title}: {e}")
                    continue

            if published_time is None:
                continue  # Skip entries without a valid date

            if current_time - published_time > age_limit:
                continue  # Skip entries older than MAX_AGE_HOURS

            articles.append({
                'feed': name,
                'title': entry.get('title', 'No title'),
                'link': entry.get('link', 'No link'),
            })
    return articles

def get_fzf_selection(options):
    """Use fzf to select an item from a list."""
    if not options:
        print("No articles to display.")
        return None

    fzf = subprocess.Popen(
        ['fzf', '--prompt=Select Article >'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, _ = fzf.communicate(input='\n'.join(options).encode('utf-8'))
    if fzf.returncode == 0:
        return stdout.decode('utf-8').strip()
    else:
        return None

def main():
    articles = fetch_feeds()
    if not articles:
        print("No articles found.")
        return

    # Format for fzf
    fzf_options = [f"{a['feed']} | {a['title']}" for a in articles]

    while True:
        selected = get_fzf_selection(fzf_options)
        if not selected:
            print("Exiting...")
            break

        # Match selected line to article
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
