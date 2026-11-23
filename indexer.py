import os
import re
import json
from collections import Counter

from utils import open_file, read_tsv, read_jsonl

# CONFIG
PODCASTS_FILE = "data/podcasts.jsonl"

INDEX_FILE = "data/index.jsonl"

INDEXED_DOCS_FILE = "data/indexed_docs.tsv"
INDEXED_DOCS_COLUMNS = ["doc_id"]

URL_PATTERN = re.compile(r"(https?://|www\.)\S+|\b\S+\.(com|fm|org|net)\S*")
STOPWORDS = {
  "i", "im", "me", "my", "myself", "we", "our",
  "ours", "ourselves", "you", "youre", "your", "yours", "yourself",
  "yourselves", "he", "him", "his", "himself",
  "she", "her", "hers", "herself", "it", "its",
  "itself", "they", "theyre", "them", "their", "theirs", "themselves",
  "what", "which", "who", "whom", "this", "that", "thats",
  "these", "those", "am", "is", "isnt", "are", "was",
  "wasnt", "were", "be", "been", "being", "have", "ive", "has",
  "had", "having", "do", "dont", "does", "doesnt", "did", "didnt",
  "doing", "a", "an", "the", "and", "but", "if",
  "or", "because", "as", "until", "while", "of",
  "at", "by", "for", "with", "about", "against",
  "between", "into", "through", "during", "before", "after",
  "above", "below", "to", "from", "up", "down",
  "in", "out", "on", "off", "over", "under",
  "again", "further", "then", "once", "here", "there", "theres",
  "when", "where", "why", "how", "all", "any",
  "both", "each", "few", "more", "most", "other",
  "some", "such", "no", "nor", "not", "only",
  "own", "same", "so", "than", "too", "very",
  "can","cant", "will", "wont", "just", "should", "now",
}


def tokenize(text):
  # Remove urls and possessive 's, split text into words
  text = URL_PATTERN.sub("", text)
  text = re.sub(r"['\u2018\u2019`]s\b", "", text.lower())
  text = re.sub(r"['\u2018\u2019`]", "", text)
  words = re.findall(r"[^\W_]+", text)
  tokens = []
  for word in words:
    # Ignore common words and single-character tokens
    if word not in STOPWORDS and len(word) > 1:
      tokens.append(word)
  return tokens


def build_document(podcast):
  # Combine show and episode text into one document
  parts = [
    podcast.get("title", ""),
    podcast.get("description", ""),
    podcast.get("category", ""),
    podcast.get("creator"),
    podcast.get("channel"),
    podcast.get("provider"),
  ]
  parts += podcast.get("hosts") or []
  parts += podcast.get("guests") or []
  for episode in podcast.get("episodes") or []:
    parts.append(episode.get("title"))
    parts.append(episode.get("description"))

  return " ".join(part for part in parts if part)


def build_index():
  # Load existing index state and select podcasts that have not been indexed
  indexed_docs = {row["doc_id"] for row in read_tsv(INDEXED_DOCS_FILE)}
  podcasts = read_jsonl(PODCASTS_FILE)

  queue = [podcast for podcast in podcasts if podcast.get("id") not in indexed_docs]

  if not queue:
    print("Nothing new to index")
    return

  index = {row["term"]: row["postings"] for row in read_jsonl(INDEX_FILE)}
  print(f"{len(queue)} new podcasts to index.")

  done = []
  try:
    print("Indexing...")
    for podcast in queue:
      doc_id = podcast.get("id")
      tokens = tokenize(build_document(podcast))
      total_tokens = len(tokens)
      if not tokens:
       continue
      # Store each term's frequency and its relative frequency in the document
      term_freq = Counter(tokens)
      for term, count in term_freq.items():
        index.setdefault(term, {})[doc_id] = [count, count / total_tokens]
      done.append(doc_id)

  except KeyboardInterrupt:
    print("Indexing interrupted by user.")

  finally:
    # Write to a temporary file first so the index is replaced atomically
    tmp_path = INDEX_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
      for term in sorted(index):
        file.write(json.dumps({"term": term, "postings": index[term]}) + "\n")
    os.replace(tmp_path, INDEX_FILE)

    # Record completed document IDs so later runs only process new podcasts
    indexed_file, indexed_writer = open_file(INDEXED_DOCS_FILE, INDEXED_DOCS_COLUMNS)
    for doc_id in done:
      indexed_writer.writerow([doc_id])
    indexed_file.close()
    print(f"Indexed {len(done)}/{len(queue)} new docs, {len(index)} terms total")


if __name__ == "__main__":
  build_index()