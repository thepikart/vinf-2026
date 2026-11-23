import json
import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.en import EnglishAnalyzer
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.search import IndexSearcher, BooleanQuery, BooleanClause, MatchAllDocsQuery
from org.apache.lucene.queryparser.classic import MultiFieldQueryParser, QueryParserBase
from org.apache.lucene.document import IntPoint, DoublePoint
from search import parse_input, parse_number_filter

LUCENE_DIR = "data/lucene_index"
JOINED_FILE = "data/spark/podcasts_wiki.jsonl"
TOP_K = 10

SEARCH_FIELDS = ["title", "description", "category", "hosts", "guests", "creator", "publisher", "episode_title", "episode_description", "wiki_summary", "wiki_genre", "host_bio"]
NUMBER_FILTERS = ["rating", "episodes", "seasons"]
DATE_FILTERS = ["began", "ended", "host_born"]

BIG = 1e9

def read_podcast(offset):
  with open(JOINED_FILE, "rb") as file:
    file.seek(offset)
    return json.loads(file.readline())


def date_range(value):
  # +2020-03: from 2020-03-01 | -2020-03: up to 2020-03-31 | =2020-03: whole month
  value = value.strip()
  sign, text = (value[0], value[1:]) if value[0] in "+-=" else ("=", value)
  parts = [int(p) for p in text.split("-")]
  year = parts[0]
  month = parts[1] if len(parts) > 1 else None
  day = parts[2] if len(parts) > 2 else None
  low = year * 10000 + (month or 1) * 100 + (day or 1)
  high = year * 10000 + (month or 12) * 100 + (day or 31)
  if sign == "+":
    return low, 99991231
  if sign == "-":
    return 0, high
  return low, high


def number_query(field, sign, n):
  if sign == "+":
    low, high = n, BIG
  elif sign == "-":
    low, high = -BIG, n
  else:
    low, high = n, n

  if field == "rating":
    return DoublePoint.newRangeQuery(field, low, high)
  return IntPoint.newRangeQuery(field, int(low), int(high))


def build_query(parser, text, filters):
  builder = BooleanQuery.Builder()
  
  if text:
    builder.add(QueryParserBase.parse(parser, text), BooleanClause.Occur.MUST)
  else:
    builder.add(MatchAllDocsQuery(), BooleanClause.Occur.MUST)

  for name, value in filters.items():
    if name in NUMBER_FILTERS:
      sign, number = parse_number_filter(name, value)
      builder.add(number_query(name, sign, number), BooleanClause.Occur.FILTER)
    elif name in DATE_FILTERS:
      low, high = date_range(value)
      builder.add(IntPoint.newRangeQuery(name, low, high), BooleanClause.Occur.FILTER)
    else:
      raise ValueError(f"Unknown switch --{name}")

  return builder.build()


def print_results(searcher, score_docs):
  if len(score_docs) == 0:
    print("No results found.")
    return

  ranked_results = list(enumerate(score_docs, start=1))

  for rank, hit in reversed(ranked_results):
    doc = searcher.storedFields().document(hit.doc)
    podcast = read_podcast(int(doc.get("offset")))
    
    print()
    print(f"Rank: {rank}, ID: {doc.get('id')}, Score: {hit.score}")
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

    wiki = podcast.get("wiki")
    if wiki:
      print(f"Wiki: genre: {wiki.get('genre')} | began: {wiki.get('began')} | ended: {wiki.get('ended')} | language: {wiki.get('language')}")
      print(f"Wiki summary: {(wiki.get('summary') or '')[:300]}...")
    for host in podcast.get("hosts_wiki") or []:
      print(f"Host wiki: {host['name']} | born: {host.get('birth_date')}, {host.get('birth_place')} | {host.get('occupation')}")

    print("Episodes:")
    for episode in podcast["episodes"] or []:
      print(f"  - {episode['title']}")
      print(f"    Date:{episode['date']} | Duration:{episode['duration']} | {'Explicit' if episode['explicit'] else 'Clean'}")
      print(f"    Description: {(episode['description'] or '')[:200]}...")
      print()
    print()
    print("--------------------------------------------------")
    print()


def search(searcher, parser, user_input):
  try:
    text, filters = parse_input(user_input)
    if not text and not filters:
      print("Enter a query or at least one filter.")
      return
    query = build_query(parser, text, filters)
  except Exception as error:
    print(error)
    return

  hits = searcher.search(query, TOP_K)
  print(f"Lucene query: {query}")
  print_results(searcher, hits.scoreDocs)


def start_search():
  lucene.initVM()
  reader = DirectoryReader.open(FSDirectory.open(Paths.get(LUCENE_DIR)))
  searcher = IndexSearcher(reader)
  parser = MultiFieldQueryParser(SEARCH_FIELDS, EnglishAnalyzer())

  while True:
    print()
    print("Search podcasts: <query> <--filters>")
    print("Query:   word | word1 word2 (OR) | w1 AND w2 | w1 NOT w2 | (w1 OR w2) AND w3")
    print("         \"exact phrase\" | field:word | field:\"phrase\" | wor* | wrd~ | word^3")
    print("Fields:  title description category hosts guests publisher episode_title episode_description")
    print("         wiki_summary wiki_genre host_bio wiki_language host_occupation host_birth_place")
    print("Filters: --rating --episodes --seasons   (+N: N or more, -N: N or less, =N: exactly N)   --began --ended --host_born   (+YYYY-MM-DD: from, -YYYY-MM-DD: up to, =YYYY-MM-DD: exact date)")
    print("Example: category:comedy AND \"true crime\" --episodes +100 --rating +4.5")
    print()
    
    user_input = input("Enter search query || \"--exit\" to quit: ").strip()
    if user_input == "--exit":
      break
    search(searcher, parser, user_input)


if __name__ == "__main__":
  start_search()