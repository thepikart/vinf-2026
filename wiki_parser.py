import re
import unicodedata

from extractor import clean_html
from utils import unescape_html

IGNORED_FIELDS = {
  "image", "image_size", "imagesize", "image_upright", "upright", "alt", "caption",
  "logo", "logo_size", "logo_alt", "signature", "signature_alt", "module", "embed",
  "image_alt", "landscape", "map", "map_caption", "map_size", "bgcolor",
}

INFOBOX_START = re.compile(r"\{\{\s*Infobox\s+([^|\n}<]+)", re.IGNORECASE)
 
DATE_TEMPLATE = re.compile(r"\{\{\s*((?:birth|death|start|end) date(?: and age2?)?)\s*\|([^{}]*)\}\}", re.IGNORECASE)

LIST_TEMPLATE = re.compile(r"\{\{\s*(?:hlist|plainlist|flatlist|ubl|unbulleted list)\s*\|([^{}]*)\}\}", re.IGNORECASE)

SUMMARY_REGEX = re.compile(r"^(.*?)(?:==|\Z)", re.DOTALL)


def parse_page(page):
  if "<redirect" in page:
    return None

  ns = re.search(r"<ns>(\d+)</ns>", page)
  if not ns or ns.group(1) != "0":
    return None

  title = re.search(r"<title>(.*?)</title>", page, re.DOTALL)
  page_id = re.search(r"<id>(\d+)</id>", page)
  text = re.search(r"<text[^>]*>(.*?)</text>", page, re.DOTALL)

  if not (title and page_id and text):
    return None

  return {
    "wiki_id": int(page_id.group(1)),
    "title": unescape_html(title.group(1)),
    "text": unescape_html(text.group(1)),
  }


def find_infobox(text):
  match = INFOBOX_START.search(text)
  if not match:
    return None
  infobox_type = match.group(1).strip().lower()
  start = match.start()
  depth = 0
  i = start
  while i < len(text) - 1:
    pair = text[i:i + 2]
    if pair == "{{":
      depth += 1
      i += 2
    elif pair == "}}":
      depth -= 1
      i += 2
      if depth == 0:
        return infobox_type, text[match.end():i - 2], (start, i)
    else:
      i += 1
  return None


def split_fields(body):
  parts = []
  current = []
  depth = 0
  i = 0
  while i < len(body):
    pair = body[i:i + 2]
    if pair in ("[[", "{{"):
      depth += 1
      current.append(pair)
      i += 2
    elif pair in ("]]", "}}"):
      depth = max(0, depth - 1)
      current.append(pair)
      i += 2
    elif body[i] == "|" and depth == 0:
      parts.append("".join(current))
      current = []
      i += 1
    else:
      current.append(body[i])
      i += 1
  parts.append("".join(current))
 
  fields = {}
  for part in parts:
    key, separator, value = part.partition("=")
    if separator:
      fields[key.strip().lower().replace(" ", "_")] = value.strip()
  return fields


def parse_date(match):
  nums = [p.strip() for p in match.group(2).split("|") if p.strip().isdigit()]
  if match.group(1).lower().endswith("age2"):
    nums = nums[3:]
  nums = nums[:3]
  if not nums:
    return ""
  return "-".join([nums[0]] + [n.zfill(2) for n in nums[1:]])

def parse_list(match):
  items = [i.strip(" *\n") for i in re.split(r"\||\n", match.group(1))]
  return ", ".join(i for i in items if i and "=" not in i)


def clean_wiki(text):
  text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
  text = re.sub(r"<ref[^>]*/>", "", text, flags=re.IGNORECASE)
  text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
  text = re.sub(r"<br\s*/?>", ", ", text, flags=re.IGNORECASE)
  text = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", text, flags=re.IGNORECASE)
  text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
  text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)

  previous = None
  while previous != text:
    previous = text
    text = DATE_TEMPLATE.sub(parse_date, text)
    text = LIST_TEMPLATE.sub(parse_list, text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)

  text = re.sub(r"'{2,}", "", text)
  return clean_html(text)


def get_summary(text, infobox_span):
  if infobox_span:
    text = text[:infobox_span[0]] + text[infobox_span[1]:]

  text = SUMMARY_REGEX.match(text).group(1)
  text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text, flags=re.IGNORECASE)
  text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

  previous = None
  while previous != text:
    previous = text
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)

  return clean_wiki(text)


def normalize_title(title):
  title = re.sub(r"\s*\(.*?\)\s*$", "", title or "")
  title = unicodedata.normalize("NFKC", title).lower()
  title = re.sub(r"[^\w\s]", "", title)
  return re.sub(r"\s+", " ", title).strip()



def extract_wiki_page(wiki_page, podcast_people=None):
  if "nfobox" not in wiki_page:
    return None
  
  page = parse_page(wiki_page)
  if not page:
    return None

  is_person = podcast_people is None or normalize_title(page["title"]) in podcast_people

  infobox = find_infobox(page["text"])
  if not infobox:
    return None
  infobox_type, infobox_body, infobox_span = infobox

  if infobox_type.startswith("podcast"):
    page_type = "podcast"
  elif is_person:
    page_type = "person"
  else:
    return None

  fields = {}
  for key, value in split_fields(infobox_body).items():
    if key in IGNORED_FIELDS:
      continue
    value = clean_wiki(value)
    if value:
      fields[key] = value

  return {
    "wiki_id": page["wiki_id"],
    "title": page["title"],
    "type": page_type,
    "fields": fields,
    "summary": get_summary(page["text"], infobox_span),
  }