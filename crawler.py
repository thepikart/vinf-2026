import os
import time
import requests
import re
import hashlib
import random
import gzip
from collections import deque
from urllib.parse import urlparse

from utils import open_file, now, read_tsv, podcast_id

# CONFIG
BASE_URL = "https://podcasts.apple.com"
URL_PATTERN = re.compile(r"/[a-z]{2}/podcast/[^/?#]+/id\d+/?")
LINK_PATTERN = re.compile(r'href="(?:https://podcasts\.apple\.com)?(/[a-z]{2}/podcast/[^/?#"]+/id\d+)')

OUTPUT_DIR = "data/pages"
HEADERS = {
  "User-Agent": "VINF-StudentBot/1.0 (FIIT STU university project; xpikartova@stuba.sk)",
  "Accept": "text/html,application/xhtml+xml",
  "Accept-Encoding": "gzip, deflate",
}
DISALLOWED_PATHS = []
MAX_PAGES = 3000

SEEDS = [
  "https://podcasts.apple.com/us/new",
]
OLD_SEEDS = ["https://podcasts.apple.com/us/genre/1511", "https://podcasts.apple.com/us/genre/1521", "https://podcasts.apple.com/us/genre/1302", "https://podcasts.apple.com/us/genre/1482", "https://podcasts.apple.com/us/genre/1310", "https://podcasts.apple.com/us/genre/1502", "https://podcasts.apple.com/sk/new", "https://podcasts.apple.com/gb/new", "https://podcasts.apple.com/ca/new"]

SITEMAP = "https://podcasts.apple.com/sitemaps_podcasts_index_podcast_1.xml"

DISCOVERED_FILE = "data/discovered.tsv"
DISCOVERED_COLUMNS = ["url", "depth", "parent_url", "discovered_at"]

PAGES_FILE = "data/pages.tsv"
PAGE_COLUMNS = ["url", "depth", "parent_url", "http_code", "fetched_at", "elapsed_ms", "size_bytes", "file"]

SITEMAPS_FILE = "data/sitemaps.tsv"
SITEMAPS_COLUMNS = ["sitemap", "urls", "new_urls", "processed_at"]

RETRY_DELAY = 5
RETRY_ATTEMPTS = 3
RETRY_CODES = [429, 500, 502, 503, 504]


def load_robots():
  # Load robots.txt and collect disallowed paths
  robots_url = BASE_URL + "/robots.txt"
  response = requests.get(robots_url, headers=HEADERS)

  for_everyone = False

  if response.status_code != 200:
    print("Failed to load robots.txt")
    return False

  for line in response.text.splitlines():
    if line.startswith("User-agent:"):
      user_agent = line.split(":", 1)[1].strip()
      # Apply disallow rules only while reading the wildcard user-agent section
      if user_agent == "*":
        for_everyone = True
      else:
        for_everyone = False
    elif for_everyone and line.startswith("Disallow:"):
      path = line.split(":", 1)[1].strip()
      if path:
        # Convert robots.txt wildcards into regex
        pattern = re.escape(path).replace(r"\*", ".*")
        DISALLOWED_PATHS.append(re.compile(pattern))

  print("Disallowed paths:", DISALLOWED_PATHS)
  return True


def is_path_allowed(path):
  # Check that a podcast path matches the expected url format and is not disallowed
  if not URL_PATTERN.match(path):
    return False

  for pattern in DISALLOWED_PATHS:
    if pattern.match(path):
      return False

  return True


def fetch_page(url):
  # Fetch URL, retrying temporary errors and request failures
  response = None
  for attempt in range(RETRY_ATTEMPTS):
    try:
      response = requests.get(url, headers=HEADERS, timeout=(5, 15))
      if response.status_code not in RETRY_CODES:
        return response
    except requests.RequestException as e:
      print("Failed:", e, url)
      response = None

    if attempt == RETRY_ATTEMPTS - 1:
      break

    delay = RETRY_DELAY
    if response is not None:
      # Respect Retry-After values, wait max two minutes
      retry_after = response.headers.get("Retry-After", "")
      if retry_after.isdigit():
        delay = min(int(retry_after), 120)
    print(f"Retrying ({attempt + 1}/{RETRY_ATTEMPTS}) after {delay} seconds...")
    time.sleep(delay)

  return response


def extract_urls(html):
  # Find podcast links in HTML and normalize them to the US store URL
  urls = set()
  for path in LINK_PATTERN.findall(html):
    if any(p.match(path) for p in DISALLOWED_PATHS):
      continue
    urls.add(BASE_URL + re.sub(r"^/[a-z]{2}/", "/us/", path))
  return urls


def save_page(url, html):
  # Save a page using hashed filename from its URL.
  name = hashlib.md5(url.encode()).hexdigest() + ".html"
  path = os.path.join(OUTPUT_DIR, name)
  with open(path, "w", encoding="utf-8", newline="") as file:
    file.write(html)
  return path


def read_sitemap(url):
  # Fetch and decode a sitemap, unless robots.txt disallows its path
  path = urlparse(url).path
  for pattern in DISALLOWED_PATHS:
    if pattern.match(path):
      print("Sitemap disallowed by robots.txt:", url)
      return None

  response = fetch_page(url)
  if response is None or response.status_code != 200:
    print("Failed to fetch sitemap:", url)
    return None

  data = response.content
  if data[:2] == b"\x1f\x8b":
    data = gzip.decompress(data)
  return data.decode("utf-8", errors="replace")


def process_sitemaps(add_url):
  # Process the next unprocessed sitemap and add its podcast URLs to the crawl
  processed_sitemaps = {row["sitemap"] for row in read_tsv(SITEMAPS_FILE)}

  # The index lists sitemap files; each file contains podcast page urls
  main_sitemap = read_sitemap(SITEMAP)
  if main_sitemap is None:
    return False

  sitemaps = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", main_sitemap)
  # Select only sitemaps not already recorded as processed.
  pending = [sitemap for sitemap in sitemaps if sitemap not in processed_sitemaps]
  if not pending:
    print("All sitemaps processed.")
    return False

  sitemap = pending[0]
  time.sleep(random.uniform(1.5, 3))
  content = read_sitemap(sitemap)
  if content is None:
    return False

  # Extract podcast urls, normalize regional urls, add them to the queue
  links = set(re.findall(BASE_URL + URL_PATTERN.pattern, content))
  print(f"Found: {list(links)[:5]}")
  time.sleep(2)
  new = sum(add_url(re.sub(r"^(https://[^/]+)/[a-z]{2}/", r"\1/us/", link), 1, sitemap) for link in links)

  # Log this sitemap so a later run can skip it
  sitemaps_file, sitemaps_writer = open_file(SITEMAPS_FILE, SITEMAPS_COLUMNS)
  sitemaps_writer.writerow([sitemap, len(links), new, now()])
  sitemaps_file.close()
  print(f"Sitemap {sitemap}: {len(links)} links, {new} new")
  return True

def crawl():
  robots_loaded = load_robots()
  if not robots_loaded:
    print("Failed to load robots.txt, stopping.")
    return
  
  os.makedirs(OUTPUT_DIR, exist_ok=True)

  # Restore discovered URLs and identify podcast IDs already saved
  discovered = [row["url"] for row in read_tsv(DISCOVERED_FILE)]
  fetched = {podcast_id(row["url"]) for row in read_tsv(PAGES_FILE)}

  # Podcast IDs deduplicate URLs (regional storefronts)
  seen = {podcast_id(url) for url in discovered}
  # Build the queue from discovered URLs that have not been saved yet
  queue = deque(url for url in discovered if podcast_id(url) not in fetched)

  print(f"Resuming: {len(seen)} known, {len(fetched)} fetched, {len(queue)} in queue")

  discovered_file, discovered_writer = open_file(DISCOVERED_FILE, DISCOVERED_COLUMNS)
  pages_file, pages_writer = open_file(PAGES_FILE, PAGE_COLUMNS)

  # track crawl depth and parent information
  rows = read_tsv(DISCOVERED_FILE)
  depths = {row["url"]: int(row["depth"]) for row in rows}
  parents = {row["url"]: row["parent_url"] for row in rows}

  def add_url(url, depth, parent):
    # Queue and record URL only if its podcast ID has not been seen
    key = podcast_id(url)
    if key in seen:
      return False
    seen.add(key)
    depths[url] = depth
    parents[url] = parent
    queue.append(url)
    discovered_writer.writerow([url, depth, parent, now()])
    # Flush each discovered URL so it can be resumed if the crawl stops
    discovered_file.flush()
    return True

  for seed in SEEDS:
    response = fetch_page(seed)
    if response is not None and response.status_code == 200:
      response.encoding = "utf-8"
      links = extract_urls(response.text)
      new = sum(add_url(link, 1, seed) for link in links)
      print(f"Seed {seed}: {len(links)} links, {new} new")
    else:
      print("Seed failed:", seed)
    time.sleep(random.uniform(1.5, 3))

  count = 0
  blocked = 0

  try:
    while count < MAX_PAGES:
      if not queue:
        # When the queue is empty, use the next sitemap to find more urls
        if not process_sitemaps(add_url):
          print("Queue empty and no more sitemaps, stopping.")
          break
        continue
      url = queue.popleft()
      if podcast_id(url) in fetched:
        # Skip entries already handled
        continue
      depth = depths[url]
      print(f"[{count + 1}] {url}")

      # request duration
      start = time.time()
      response = fetch_page(url)
      elapsed = int((time.time() - start) * 1000)

      if response is not None and response.status_code in (403, 429):
        blocked += 1
        # Stop after 5 blocks
        if blocked >= 5:
          print("Blocked 5 times in a row, stopping.")
          break
      else:
        blocked = 0

      # Record the page in the log, whether successful or not
      if response is None or response.status_code != 200:
        code = response.status_code if response is not None else ""
        pages_writer.writerow([url, depth, parents.get(url, ""), code, now(), elapsed, "", ""])
        pages_file.flush()
        fetched.add(podcast_id(url))
      else:
        response.encoding = "utf-8"
        html = response.text
        path = save_page(url, html)
        pages_writer.writerow([url, depth, parents.get(url, ""), response.status_code, now(), elapsed, len(response.content), path])
        pages_file.flush()
        fetched.add(podcast_id(url))
        count += 1
        for link in extract_urls(html):
          add_url(link, depth + 1, url)
      
      time.sleep(random.uniform(1.5, 3))

  except KeyboardInterrupt:
      print("Crawl interrupted by user.")
  finally:
    discovered_file.close()
    pages_file.close()
    print(count, "pages saved,", len(queue), "URLs still in queue")


if __name__ == "__main__":
  crawl()