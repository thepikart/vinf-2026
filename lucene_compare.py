import time
import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.en import EnglishAnalyzer
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.queryparser.classic import MultiFieldQueryParser

import search as old
import lucene_search as new

QUERIES = [
  "true crime",
  "space AND exploration",
  "cooking OR baking",
  "startup AND funding",
  "basketball NOT fantasy",
  "health AND nutrition NOT diet",
  "philosophy --episodes +100",
  "investing NOT crypto --rating +4.5",
  "mental AND health --episodes +100 --rating +4.5",
  "language learning --episodes -50",
]

IDF = "classic"
TF = "count"
RUNS = 5

captured = []
old.TOP_K = 10**9
old.print_results = lambda top, idf, tf: captured.extend(top)


def timed(fnc):
  fnc()
  start = time.perf_counter()
  for _ in range(RUNS):
    result = fnc()
  return result, (time.perf_counter() - start) / RUNS * 1000


def main():
  lucene.initVM()

  old.load_index()
  
  reader = DirectoryReader.open(FSDirectory.open(Paths.get(new.LUCENE_DIR)))
  searcher = IndexSearcher(reader)
  parser = MultiFieldQueryParser(new.SEARCH_FIELDS, EnglishAnalyzer())

  for user_input in QUERIES:
    text, filters = old.parse_input(user_input)

    def old_run():
      captured.clear()
      old.search(text, filters, IDF, TF)
      return len(captured), [id for id, _ in captured[:10]]

    def new_run():
      query = new.build_query(parser, text, filters)
      total = searcher.count(query)
      hits = searcher.search(query, 10).scoreDocs
      return total, [searcher.storedFields().document(h.doc).get("id") for h in hits]

    (old_total, old_ids), old_ms = timed(old_run)
    (new_total, new_ids), new_ms = timed(new_run)
    overlap = len(set(old_ids) & set(new_ids))

    print("=" * 70)
    print(f"QUERY: {user_input}")
    print(f"  old: {old_total:>6} hits, {old_ms:7.2f} ms   (idf={IDF}, tf={TF})")
    print(f"  new: {new_total:>6} hits, {new_ms:7.2f} ms   (BM25)")
    print(f"  top-10 overlap: {overlap}/10")
    print("  old top 5:")
    for i, id in enumerate(old_ids[:10], 1):
      print(f"    {i}. {old.PODCASTS[id]["title"] if id in old.PODCASTS else id}")
    print("  new top 5:")
    for i, id in enumerate(new_ids[:10], 1):
      print(f"    {i}. {old.PODCASTS[id]["title"] if id in old.PODCASTS else id}")


if __name__ == "__main__":
  main()