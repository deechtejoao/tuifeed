#!/usr/bin/env python3

from __future__ import annotations
import asyncio, json, logging, time, aiohttp, feedparser, webbrowser, sys, argparse, listparser
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from subprocess import PIPE, Popen
from typing import Dict, List, Optional, Set

# CONFIG
MAX_AGE_HOURS = 24
REQUEST_TIMEOUT = 5
RETRY_ATTEMPTS = 2
MAX_CONCURRENCY = 20
CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "rss_cache.json"
HEADERS = {"User-Agent": "tuifeed/3.0 (aiohttp)"}
CONFIG_DIR  = Path.home() / ".config" / "tuifeed"
CONFIG_PATH = CONFIG_DIR / "config.json"

# LOGGING
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s"
)
log = logging.getLogger("tuifeed")


# DATA MODEL
@dataclass(slots=True, frozen=True)
class Article:
    feed: str
    title: str
    link: str
    timestamp: datetime

    @classmethod
    def from_dict(cls, d: Dict) -> "Article":
        return cls(
            d["feed"], d["title"], d["link"], datetime.fromisoformat(d["timestamp"])
        )

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def __str__(self) -> str:
        return f"{self.feed} | {self.title}"


# CACHE
def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(exist_ok=True)


def read_cache(current_feeds: Set[str]) -> List[Dict]:
    if not CACHE_FILE.exists():
        return []
    try:
        raw = json.load(CACHE_FILE.open())
        age_limit = timedelta(hours=MAX_AGE_HOURS)
        now = datetime.now()
        return [
            a
            for a in raw.get("articles", [])
            if a["feed"] in current_feeds
            and now - datetime.fromisoformat(a["timestamp"]) <= age_limit
        ]
    except Exception as e:
        log.error("cache read: %s", e)
        return []


def write_cache(arts: List[Dict]) -> None:
    try:
        _ensure_cache_dir()
        json.dump(
            {"timestamp": datetime.now().isoformat(), "articles": arts},
            CACHE_FILE.open("w"),
            indent=2,
        )
    except Exception as e:
        log.error("cache write: %s", e)


# FETCH
AGE_LIMIT = timedelta(hours=MAX_AGE_HOURS)


def _is_fresh(e) -> bool:
    try:
        return (
            datetime.now() - datetime.fromtimestamp(time.mktime(e.published_parsed))
            <= AGE_LIMIT
        )
    except Exception:
        return False


async def _fetch(name: str, url: str, session: aiohttp.ClientSession) -> List[Dict]:
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            async with session.get(url, timeout=REQUEST_TIMEOUT) as r:
                r.raise_for_status()
                raw = await r.read()
            p = feedparser.parse(raw)
            if p.bozo:
                raise ValueError(p.bozo_exception)
            return [
                {
                    "feed": name,
                    "title": it.get("title", "No title"),
                    "link": it.get("link", "No link"),
                    "timestamp": datetime.fromtimestamp(
                        time.mktime(it.published_parsed)
                    ).isoformat(),
                }
                for it in p.entries
                if _is_fresh(it)
            ]
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                log.warning("retry %s (%d)", name, attempt + 1)
                await asyncio.sleep(2**attempt)
            else:
                log.error("fail %s: %s", name, e)
                return []


async def fetch_all(feeds: List[Dict]) -> List[Dict]:
    conn = aiohttp.TCPConnector(limit=MAX_CONCURRENCY)
    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as sess:
        tasks = [
            asyncio.create_task(_fetch(f["name"], f["url"], sess))
            for f in feeds
            if f.get("name") and f.get("url")
        ]
        return [item for sub in await asyncio.gather(*tasks) for item in sub]


# MERGE
def merge(new: List[Dict], old: List[Dict]) -> List[Dict]:
    seen, merged = set(), []
    for a in new + old:
        l = a["link"]
        if l and l != "No link" and l not in seen:
            seen.add(l)
            merged.append(a)
    return merged


# FZF
def _fzf_exists() -> bool:
    try:
        Popen(["fzf", "--version"], stdout=PIPE, stderr=PIPE)
        return True
    except FileNotFoundError:
        return False


def choose(opts: List[str]) -> Optional[str]:
    if not opts or not _fzf_exists():
        return None
    try:
        f = Popen(
            ["fzf", "--prompt=Select Article >"], stdin=PIPE, stdout=PIPE, text=True
        )
        out, _ = f.communicate("\n".join(opts))
        return out.strip() if f.returncode == 0 else None
    except Exception as e:
        log.error("fzf: %s", e)
        return None


# MAIN
def _config() -> Dict:
    config_paths = [
        Path.home() / ".config" / "tuifeed" / "config.json",
        Path("config.json"),
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                return json.load(config_path.open())
            except Exception as e:
                log.error("config: %s", e)
    log.error("No valid config file found.")
    return {}


async def async_main() -> None:
    feeds = _config().get("feeds", [])
    if not feeds:
        log.error("no feeds")
        return
    cached = read_cache({f["name"] for f in feeds if "name" in f})
    fresh = await fetch_all(feeds)
    arts = merge(fresh, cached)
    if arts:
        write_cache(arts)
    elif CACHE_FILE.exists():
        CACHE_FILE.unlink()
    if not arts:
        log.info("no articles")
        return
    opts = [f"{a['feed']} | {a['title']}" for a in arts]
    while True:
        sel = choose(opts)
        if not sel:
            break
        for a in arts:
            if str(Article(**a)) == sel and a["link"] != "No link":
                log.info("open %s", a["link"])
                webbrowser.open(a["link"])
                break


def import_opml(opml_file: Path) -> None:
    if listparser is None:
        sys.exit("Error: pip install listparser to enable OPML import")

    if not opml_file.exists():
        sys.exit(f"Error: OPML file not found -> {opml_file}")

    result = listparser.parse(opml_file.read_text(encoding="utf-8"))
    feeds = [
        {"name": f["title"], "url": f["url"]}
        for f in result.feeds
        if f.get("title") and f.get("url")
    ]
    if not feeds:
        sys.exit("Error: no valid feeds inside OPML")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"feeds": feeds}, indent=2), encoding="utf-8")
    log.info("Imported %d feeds -> %s", len(feeds), CONFIG_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tuifeed")
    parser.add_argument(
        "-p",
        "--opml",
        metavar="FILE",
        help="import OPML and write ~/.config/tuifeed/config.json",
    )
    args = parser.parse_args()

    # OPML import mode
    if args.opml:
        import_opml(Path(args.opml).expanduser())
        return

    # normal execution
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
