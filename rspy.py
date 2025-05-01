import feedparser
import json
import subprocess
from colorama import Fore, Style, init
from bs4 import BeautifulSoup
import webbrowser

init()  # Initialize colorama

FEED_COLORS = [Fore.RED, Fore.GREEN, Fore.BLUE, Fore.YELLOW, Fore.MAGENTA, Fore.CYAN, Fore.WHITE]

def clean_html(html):
    """Remove HTML tags from a string."""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def fetch_feeds():
    """Fetch and parse all feeds from config.json."""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found.")
        return []
    except json.JSONDecodeError:
        print("Error: config.json is not valid JSON.")
        return []

    articles = []
    for idx, feed in enumerate(config.get('feeds', [])):
        name = feed.get('name')
        url = feed.get('url')
        if not name or not url:
            continue

        color = FEED_COLORS[idx % len(FEED_COLORS)]
        print(f"{color}Fetching {name}...{Style.RESET_ALL}")

        parsed_feed = feedparser.parse(url)
        if parsed_feed.bozo:
            print(f"{color}Error fetching {name}: {parsed_feed.bozo_exception}{Style.RESET_ALL}")
            continue

        for entry in parsed_feed.entries[:5]:
            articles.append({
                'feed': name,
                'title': entry.get('title', 'No title'),
                'link': entry.get('link', 'No link'),
                'summary': clean_html(entry.get('summary', 'No summary')),
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
