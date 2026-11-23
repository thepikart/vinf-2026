import math
import json
from utils import read_jsonl
from indexer import tokenize

# CONFIG
INDEX_FILE = "data/index.jsonl"
INDEX = None

PODCASTS_FILE = "data/podcasts.jsonl"
PODCASTS = None

MAX_DF = 0

TOP_K = 10

def calculate_idf(term, idf_method):
  n = len(PODCASTS)
  df = len(INDEX.get(term, ()))
  if df == 0:
    return 0.0
  if idf_method == "classic":
    return math.log(n / df)
  if idf_method == "smooth":
    return math.log(1 + n / (1 + df)) + 1
  if idf_method == "squared":
    return math.log(n / df) ** 2
  if idf_method == "max":
    return math.log(1 + MAX_DF / df)
  
  raise ValueError(f"Wrong idf method: {idf_method}")
 

def load_index():
  # Load term postings and cache podcast fields needed for filtering
  global INDEX, PODCASTS, MAX_DF
  INDEX = {row["term"]: row["postings"] for row in read_jsonl(INDEX_FILE)}
  MAX_DF = max(len(postings) for postings in INDEX.values())
  PODCASTS = {}
  with open(PODCASTS_FILE, "rb") as file:
    offset = 0
    for line in file:
      if line.strip():
        podcast = json.loads(line)
        if podcast["id"] not in PODCASTS:
          # Keep the JSONL byte offset so full records can be loaded only for results
          PODCASTS[podcast["id"]] = {
            "rating": podcast.get("rating"),
            "episodes_count": podcast.get("episodes_count"),
            "seasons_count": podcast.get("seasons_count"),
            "content_rating": podcast.get("content_rating"),
            "category": podcast.get("category"),
            "title": (podcast.get("title") or ""),
            "host": " | ".join(podcast.get("hosts") or []),
            "publisher": " | ".join(p for p in (podcast.get("channel"), podcast.get("provider")) if p),
            "offset": offset
          }
      offset += len(line)


def read_podcast(id):
  # Load one full podcast record from its saved JSONL byte offset
  with open(PODCASTS_FILE, "rb") as file:
    file.seek(PODCASTS[id]["offset"])
    return json.loads(file.readline())


def split_query(query):
  include, exclude, and_mode, not_mode = [], [], False, False
  for word in query.split():
    if word == "AND":
      and_mode = True
    elif word == "OR":
      continue
    elif word == "NOT":
      not_mode = True
    else:
      target = exclude if not_mode else include
      target.extend(tokenize(word))
      not_mode = False
  return include, exclude, and_mode


def parse_input(user_input):
  # Split the free-text query from switches written as "--name value"
  parts = (" " + user_input).split(" --")
  query = parts[0].strip()
  filters = {}
  for part in parts[1:]:
    name, _, value = part.partition(" ")
    filters[name.strip()] = value.strip()
  return query, filters


def parse_number_filter(name, value):
  # Parse +N, -N, and =N filter values into an operator and number.
  sign, number = value[:1], value[1:].strip()
  try:
    if sign in ("+", "-"):
      return sign, float(number)
    else:
      return "=", float(number)
  except ValueError:
    raise ValueError(f"Bad value for --{name}: '{value}'. Use +N (more than), -N (less than) or =N (equal), e.g. --{name} +4.5")


def parse_filters(filters):
  # Validate filter names and convert values to comparable forms
  parsed = {}
  for name, value in filters.items():
    if name in ("episodes", "seasons", "rating"):
      parsed[name] = parse_number_filter(name, value)
    elif name == "content-rating":
      if value.lower() not in ("explicit", "clean"):
        raise ValueError("--content-rating must be explicit or clean")
      parsed[name] = value.lower()
    elif name in ("category", "title", "host", "publisher"):
      parsed[name] = value.lower()
    else:
      raise ValueError(f"Unknown switch --{name}")
  return parsed


def passes_number(actual, condition):
  # Compare a podcast's numeric value using a parsed filter condition
  if actual is None:
    return False
  sign, number = condition
  if sign == "+":
    return actual >= number
  if sign == "-":
    return actual <= number
  return actual == number


def filter_podcast(podcast, filters):
  # Require a podcast to satisfy every provided filter
  if "episodes" in filters and not passes_number(podcast["episodes_count"], filters["episodes"]):
    return False
  if "seasons" in filters and not passes_number(podcast["seasons_count"], filters["seasons"]):
    return False
  if "rating" in filters and not passes_number(podcast["rating"], filters["rating"]):
    return False
  if "content-rating" in filters and (podcast["content_rating"] or "").lower() != filters["content-rating"]:
    return False
  if "category" in filters and filters["category"] not in (podcast["category"] or "").lower():
    return False
  if "title" in filters and filters["title"] not in (podcast["title"] or "").lower():
    return False
  if "host" in filters and filters["host"] not in (podcast["host"] or "").lower():
    return False
  if "publisher" in filters and filters["publisher"] not in (podcast["publisher"] or "").lower():
    return False

  return True


def print_results(top_results, idf_method, tf_method):
  # Print full podcast details in rank order.
  if len(top_results) == 0:
    print("No results found.")
    return

  ranked_results = list(enumerate(top_results, start=1))
  for rank, (id, score) in reversed(ranked_results):
    podcast = read_podcast(id)
    print()
    print(f"Rank: {rank}, ID: {id}, Score: {score} (idf: {idf_method}, tf: {tf_method})")
    print(f"Title: {podcast['title']}")
    print(f"Category: {podcast['category']} | Rating: {podcast['rating']} | Content rating: {podcast['content_rating']}")
    print(f"Start year: {podcast['start_year']} | End year: {podcast['end_year']}")
    print(f"Update frequency: {podcast['update_frequency']} | Episodes: {podcast['episodes_count']} | Seasons: {podcast['seasons_count']}")
    print(f"Description: {(podcast['description'] or '')[:300]}...")
    print(f"Hosts: {podcast['hosts']}")
    print(f"Guests: {podcast['guests']}")
    print(f"Creator: {podcast['creator']} | Channel: {podcast['channel']} | Provider: {podcast['provider']}")
    print(f"Podcast url: {podcast['url']}")
    print(f"Show website: {podcast['show_website']}")
    
    print("Episodes:")
    for episode in podcast['episodes'] or []:
      print(f"  - {episode['title']}")
      print(f"    Date:{episode['date']} | Duration:{episode['duration']} | {'Explicit' if episode['explicit'] else 'Clean'}")
      print(f"    Description: {(episode['description'] or '')[:200]}...")
      print()
    print()
    print("--------------------------------------------------")
    print()


def search(query, filters, idf_method, tf_method):
  # Validate filters, rank matching podcasts, and print the top results
  try:
    filters = parse_filters(filters)
  except Exception as error:
    print(error)
    return

  include, exclude, and_mode = split_query(query)
  terms = list(dict.fromkeys(include))

  if not terms:
    if not filters:
      print("Enter a query or at least one filter.")
      return
    # Filter-only searches start with the highest-rated podcasts
    results = sorted(
      ((id, p["rating"] or 0) for id, p in PODCASTS.items()),
      key=lambda x: x[1], reverse=True
    )
  else:
    scores = {}
    # Select raw count or normalized term frequency for the score
    tf_pos = 0 if tf_method == "count" else 1
    for term in terms:
      if term not in INDEX:
        continue
      idf = calculate_idf(term, idf_method)
      for id, entry in INDEX[term].items():
        scores[id] = scores.get(id, 0) + entry[tf_pos] * idf

    # AND: keep only documents that contain every term
    if and_mode:
      scores = {id: s for id, s in scores.items() if all(id in INDEX.get(t, {}) for t in terms)}

    # NOT: drop documents that contain any excluded term
    for term in exclude:
      for id in INDEX.get(term, {}):
        scores.pop(id, None)

    results = sorted(scores.items(), key=lambda x: x[1], reverse=True)

  top_results = []
  for id, score in results:
    # Apply filters after text relevance ranking.
    if not filter_podcast(PODCASTS[id], filters):
      continue
    top_results.append((id, score))
    if len(top_results) >= TOP_K:
      break

  print_results(top_results, idf_method, tf_method)

  
def start_search():
  # Load search data, then accept queries and filter options interactively
  try:
    print("Loading...")
    load_index()

    idf_method = "classic"
    tf_method = "count"

    while True:
      print()
      print("Search podcasts: query  --episodes +/-N  --seasons +/-N  --rating +/-N  --content-rating clean/explicit  --category text  --title text  --host text  --publisher text")
      user_input = input("Enter search query || \"--exit\" to quit || \"--idf classic/smooth/squared/max\" to change idf method || \"--tf count/normalized\" to change tf method: ").strip()

      if user_input == "--exit":
        break
      if user_input.startswith("--idf"):
        # Change the idf method for next searches
        _, _, value = user_input.partition(" ")
        idf_method = value.strip()
        continue
      if user_input.startswith("--tf"):
        # Change between raw count and normalized tf
        _, _, value = user_input.partition(" ")
        value = value.strip()
        tf_method = value
        continue

      query, filters = parse_input(user_input)
      search(query, filters, idf_method, tf_method)
  except KeyboardInterrupt:
    print("\nSearch interrupted by user.")
  finally:
    print("Exiting search.")


if __name__ == "__main__":
  start_search()