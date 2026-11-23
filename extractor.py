import re
import json
import unicodedata
from datetime import datetime, date, timedelta

from utils import open_file, now, read_tsv, podcast_id, unescape_html

# CONFIG
PAGES_FILE = "data/pages.tsv"
PODCASTS_FILE = "data/podcasts.jsonl"

PROCESSED_FILE = "data/processed.tsv"
PROCESSED_COLUMNS = ["url", "status", "processed_at"]

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u00ad\ufeff]")

def clean_html(text):
  # Strip markup and normalize text
  text = re.sub(r"<.*?>", "", text, flags=re.DOTALL)
  text = unescape_html(text)
  text = unicodedata.normalize("NFKC", text)
  text = INVISIBLE.sub("", text)
  text = re.sub(r"\s+", " ", text).strip()
  return text

def extract_pattern(page, regex):
  # Return cleaned text from the first regex capture, or None if it does not match.
  pattern = re.compile(regex, re.IGNORECASE | re.DOTALL)
  match = pattern.search(page)
  if match:
    return clean_html(match.group(1))
  return None

def get_metadata(page):
  # Read metadata labels (category, update frequency)
  ul = re.search(r'<ul class="metadata[^"]*">(.*?)</ul>', page, re.DOTALL)
  if not ul:
    return []
  texts = []
  for attrs, inner in re.findall(r'<li([^>]*)>(.*?)</li>', ul.group(1), re.DOTALL):
    if "aria-label" in attrs or 'class="star' in inner:
      continue
    text = clean_html(inner)
    if text:
      texts.append(text)
  return texts

def get_category(page):
  # Use the first metadata value as the podcast category
  texts = get_metadata(page)
  return texts[0] if texts else None

def get_update_frequency(page):
  # Use the second metadata value as the update frequency
  texts = get_metadata(page)
  return texts[1] if len(texts) > 1 else None

def get_hosts(page):
  # Extract list of hosts
  pattern = re.compile(r"<h3 data-testid=\"ellipse-lockup__title\"[^>]*>([^<]+)</h3>\s*</span>\s*</div>\s*<p data-testid=\"ellipse-lockup__sub-title\"[^>]*>\s*<span[^>]*>Host</span>", re.IGNORECASE | re.DOTALL)
  matches = pattern.findall(page)
  return [clean_html(match) for match in matches] if matches else None

def get_guests(page):
  # Extract list of guests
  pattern = re.compile(r"<h3 data-testid=\"ellipse-lockup__title\"[^>]*>([^<]+)</h3>\s*</span>\s*</div>\s*<p data-testid=\"ellipse-lockup__sub-title\"[^>]*>\s*<span[^>]*>Guest</span>", re.IGNORECASE | re.DOTALL)
  matches = pattern.findall(page)
  return [clean_html(match) for match in matches] if matches else None

def get_years_active(page):
  # Parse the show's active years, splitting it into start and end years
  years_active = extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Years Active\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>")
  if years_active:
    if "-" in years_active:
      start_year, end_year = years_active.split("-")
      return {"start_year": int(start_year.strip()), "end_year": int(end_year.strip())}
    else:
      return {"start_year": int(years_active.strip()), "end_year": None}
  return {"start_year": None, "end_year": None}

def get_duration(duration_raw):
  # Convert an episode duration with hour/minute units to total minutes
  if not duration_raw:
    return None
  
  duration_split = duration_raw.split(" ")
  hours_pattern = re.compile(r"(\d+)\s*h")
  minutes_pattern = re.compile(r"(\d+)\s*m")
  
  hours = 0
  minutes = 0
  
  for part in duration_split:
    if match := hours_pattern.match(part):
      hours = int(match.group(1))
    elif match := minutes_pattern.match(part):
      minutes = int(match.group(1))
  
  return hours * 60 + minutes if (hours or minutes) else None

def get_date(episode_block, fetched_at):
  # Convert the displayed episode date to ISO format using the fetch time saved as metadata
  date_raw = extract_pattern(episode_block, r"<span class=\"eyebrow-text[^\"]*[^>]*>(.*?)</span>")
  if not date_raw:
    return None
  fetched = datetime.fromisoformat(fetched_at)
  text = re.split(r"[·•]", date_raw)[0].strip()

  # "6d ago" / "23h ago"
  match = re.match(r"(\d+)([hd]) ago$", text)
  if match:
    n = int(match.group(1))
    delta = timedelta(hours=n) if match.group(2) == "h" else timedelta(days=n)
    return (fetched - delta).date().isoformat()

  # "09/29/2025"
  match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", text)
  if match:
    try:
      return date(int(match.group(3)), int(match.group(1)), int(match.group(2))).isoformat()
    except ValueError:
      return None

  # "May 25"
  match = re.match(r"([A-Za-z]{3}) (\d{1,2})$", text)
  if match and match.group(1).lower() in MONTHS:
    month = MONTHS.index(match.group(1).lower()) + 1
    day = int(match.group(2))
    try:
      # The page omits the year, so use the year when it was fetched
      result = date(fetched.year, month, day)
    except ValueError:
      return None
    return result.isoformat()

  return None

def get_explicit(episode_block):
  # Check if episode is explicit by checking explicit-content badge.
  explicit_pattern = re.compile(r"<span data-testid=\"explicit-badge\"[^>]*>", re.IGNORECASE | re.DOTALL)
  match = explicit_pattern.search(episode_block)
  return bool(match)

def get_e_s_count(page, pattern):
  # Parse episode/season counts
  count = extract_pattern(page, pattern)
  if count:
    # Convert values such as "1.2K" into an integer count.
    if count.endswith("K"):
      count = int(float(count[:-1]) * 1000)
    else:
      count = int(count)
    return count
  return None

def extract_podcast(page):
  # Collect the podcast fields from the page.
  rating = extract_pattern(page, r"<div class=\"stats__main[^>]*>(.*?)</div>")
  rating = float(rating) if rating else None

  years_active = get_years_active(page)

  return {
    "title": extract_pattern(page, r"<h1[^>]*>(.*?)</h1>"),
    "description": extract_pattern(page, r"<div data-testid=\"paragraphs\"[^>]*>(.*?)</div>"),
    "category": get_category(page),
    "update_frequency": get_update_frequency(page),
    "rating": rating,
    "creator": extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Creator\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "episodes_count": get_e_s_count(page, r"<div class=\"title[^\"]*\">\s*Episodes\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "seasons_count": get_e_s_count(page, r"<div class=\"title[^\"]*\">\s*Seasons\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "start_year": years_active["start_year"],
    "end_year": years_active["end_year"],
    "content_rating": extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Rating\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "channel": extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Channel\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "provider": extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Provider\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>(.*?)</div>"),
    "show_website": extract_pattern(page, r"<div class=\"title[^\"]*\">\s*Show Website\s*</div>\s*<div class=\"content[^\"]*\"[^>]*>\s*<a[^>]*href=\"(.*?)\""),
    "hosts": get_hosts(page),
    "guests": get_guests(page)
  }

def extract_episodes(page, fetched_at):
  # Parse each episode entry and return its details as a list of dictionaries.
  episodes = []
  episodes_list_pattern = re.compile(r"<ol data-testid=\"episodes-list\"[^>]*>(.*?)</ol>", re.IGNORECASE | re.DOTALL)
  episodes_list = episodes_list_pattern.search(page)

  if not episodes_list:
    return episodes
  episodes_list = episodes_list.group(1)

  for episode_block in re.findall(r"<li[^>]*>.*?<\/li>", episodes_list, re.IGNORECASE | re.DOTALL):

    episodes.append({
      "title": extract_pattern(episode_block, r"<span class=\"episode-details__title-text\"[^>]*>(.*?)</span>"),
      "description": extract_pattern(episode_block, r"<div class=\"episode-details__summary[^\"]*\"[^>]*>.*?<span[^>]*>(.*?)</span>.*</div>"),
      "duration": get_duration(extract_pattern(episode_block, r"<span[^>]*class=\"detailed-play-button__text[^\"]*\"[^>]*>(.*?)</span>")),
      "date": get_date(episode_block, fetched_at),
      "explicit": get_explicit(episode_block)
    })

  return episodes
  

def extract():
  # Process successful, unprocessed pages and append their podcast data as JSONL
  processed = {row["url"] for row in read_tsv(PROCESSED_FILE)}
  # Only queue pages with a saved HTML file and a successful HTTP response
  queue = [row for row in read_tsv(PAGES_FILE) if row["http_code"] == "200" and row["file"] and row["url"] not in processed]

  print(len(queue), "pages to process")

  processed_file, processed_writer = open_file(PROCESSED_FILE, PROCESSED_COLUMNS)
  podcasts_file = open(PODCASTS_FILE, "a", encoding="utf-8")

  try:
    count = 0
    for row in queue:
      url = row["url"]
      try:
        with open(row["file"], "r", encoding="utf-8") as file:
          page = file.read()
        # Combine show metadata and episode details into one JSON record
        podcast_metadata = extract_podcast(page)
        episodes = extract_episodes(page, row["fetched_at"])
        podcast = {
          "id": podcast_id(row["url"]),
          "url": row["url"], 
          **podcast_metadata, 
          "episodes": episodes
        }
        podcasts_file.write(json.dumps(podcast, ensure_ascii=False) + "\n")
        podcasts_file.flush()
        status = "processed"

      except Exception as e:
        print("  error:", url, e)
        status = "error"

      # Record each outcome so completed or failed pages are not queued again
      processed_writer.writerow([url, status, now()])
      processed_file.flush()
      print(f"[{count + 1}] Processed: {row['url']} Status: {status}")
      count += 1

    print("Processing complete.")
  except Exception as e:
    print("An error occurred:", e)
  except KeyboardInterrupt:
    print("Processing interrupted by user.")
  finally:
    processed_file.close()
    podcasts_file.close()

if __name__ == "__main__": 
  extract()
