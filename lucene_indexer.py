import json
import lucene
from java.nio.file import Paths
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.analysis.en import EnglishAnalyzer
from org.apache.lucene.document import Document, Field, TextField, StringField, StoredField, IntPoint, DoublePoint
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
import re

JOINED_FILE = "data/spark/podcasts_wiki.jsonl"
LUCENE_DIR = "data/lucene_index"


def date_parser(value):
  # "2025-03-18" -> 20250318, "2025-03" -> 20250301, "2025" -> 20250101
  match = re.search(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value or "")
  if not match:
    return None
  year, month, day = match.groups()
  return int(year) * 10000 + int(month or 1) * 100 + int(day or 1)


def add_text(doc, name, value, store=False):
  if value:
    doc.add(TextField(name, str(value), Field.Store.YES if store else Field.Store.NO))


def build_doc(podcast, offset):
  doc = Document()
  doc.add(StringField("id", podcast["id"], Field.Store.YES))
  doc.add(StoredField("offset", str(offset)))

  # scored text
  add_text(doc, "title", podcast.get("title"))
  add_text(doc, "description", podcast.get("description"))
  add_text(doc, "category", podcast.get("category"))

  for host in podcast.get("hosts") or []:
    add_text(doc, "hosts", host)

  for guest in podcast.get("guests") or []:
    add_text(doc, "guests", guest)

  for publisher in (podcast.get("channel"), podcast.get("provider")):
    add_text(doc, "publisher", publisher)

  for episode in podcast.get("episodes") or []:
    add_text(doc, "episode_title", episode.get("title"))
    add_text(doc, "episode_description", episode.get("description"))

  wiki = podcast.get("wiki") or {}
  add_text(doc, "wiki_summary", wiki.get("summary"))
  add_text(doc, "wiki_genre", wiki.get("genre"))
  for host in podcast.get("hosts_wiki") or []:
    add_text(doc, "host_bio", host.get("summary"))

  add_text(doc, "creator", podcast.get("creator"))


  # filters
  if podcast.get("rating") is not None:
    doc.add(DoublePoint("rating", [float(podcast["rating"])]))

  if podcast.get("episodes_count") is not None:
    doc.add(IntPoint("episodes", [int(podcast["episodes_count"])]))

  if podcast.get("seasons_count") is not None:
    doc.add(IntPoint("seasons", [int(podcast["seasons_count"])]))

  if podcast.get("content_rating"):
    doc.add(StringField("content_rating", podcast["content_rating"].lower(), Field.Store.NO))

  began = date_parser(wiki.get("began"))
  if began is not None:
    doc.add(IntPoint("began", [began]))
  ended = date_parser(wiki.get("ended"))
  if ended is not None:
    doc.add(IntPoint("ended", [ended]))
  add_text(doc, "wiki_language", wiki.get("language"))

  for host in podcast.get("hosts_wiki") or []:
    add_text(doc, "host_occupation", host.get("occupation"))
    add_text(doc, "host_birth_place", host.get("birth_place"))
    birth = date_parser(host.get("birth_date"))
    if birth is not None:
      doc.add(IntPoint("host_born", [birth]))

  return doc


def build_index():
  lucene.initVM()
  config = IndexWriterConfig(EnglishAnalyzer())
  config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
  writer = IndexWriter(FSDirectory.open(Paths.get(LUCENE_DIR)), config)

  offset = 0
  count = 0
  
  with open(JOINED_FILE, "rb") as file:
    for line in file:
      if line.strip():
        podcast = json.loads(line)
        writer.addDocument(build_doc(podcast, offset))
        count += 1
      offset += len(line)

  writer.close()
  print(count, "documents indexed")


if __name__ == "__main__":
  build_index()