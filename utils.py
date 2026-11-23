import os
import csv
import re
import json
from datetime import datetime, timezone

def open_file(path, columns):
  # Append to a TSV file and write column names if it is empty
  is_new = not os.path.exists(path) or os.path.getsize(path) == 0
  file = open(path, "a", encoding="utf-8", newline="")
  writer = csv.writer(file, delimiter="\t")
  if is_new:
    writer.writerow(columns)
  return file, writer

def now():
  # Return the current time as an ISO-formatted string
  return datetime.now(timezone.utc).isoformat(timespec="seconds")

def read_tsv(path):
  # Read TSV rows as dictionaries, return an empty list if the file is missing
  if not os.path.exists(path):
    return []
  with open(path, encoding="utf-8", newline="") as file:
    return list(csv.DictReader(file, delimiter="\t"))

def read_jsonl(path):
  # Parse each line as JSON, return an empty list if the file is missing
  if not os.path.exists(path):
    return []
  with open(path, encoding="utf-8") as file:
    return [json.loads(line) for line in file]

def podcast_id(url):
  # Extract the podcast ID from the URL
  match = re.search(r"/(id\d+)", url)
  return match.group(1) if match else url


def unescape_html(text):
  # Replace common HTML entities using regex
  text = text.replace('&amp;', '&')
  text = text.replace('&lt;', '<')
  text = text.replace('&gt;', '>')
  text = text.replace('&quot;', '"')
  text = text.replace('&apos;', "'")
  text = text.replace('&nbsp;', '\xa0')
  
  # Numeric entities: &#123; and &#xAB;
  text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
  text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
  
  return text