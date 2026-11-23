import os
import glob
import json
import shutil
from pyspark.sql import SparkSession
from utils import read_jsonl
from wiki_parser import normalize_title

spark = (SparkSession.builder
         .master("local[8]")
         .appName("wiki_extract")
         .config("spark.driver.memory", "4g")
         .getOrCreate())
sc = spark.sparkContext

WIKI_DUMP = "data/wiki/enwiki-latest-pages-articles.xml.bz2"
PODCASTS_FILE = "data/spark/podcasts.jsonl"
 
SPARK_WIKI_DIR = "data/spark/wiki"
SPARK_WIKI_FILE = "data/spark/wiki.jsonl"


def extract_wiki(record):
  import wiki_parser

  start = record.find("<page>")
  if start < 0:
    return None

  try:
    page = wiki_parser.extract_wiki_page(record[start:], podcast_people.value)
    if page:
      print("extracted page:", page["title"])
      return json.dumps(page, ensure_ascii=False)
    return None
  except Exception as e:
    print("error:", e)
    return None


names = set()
for podcast in read_jsonl(PODCASTS_FILE):
  for name in (podcast.get("hosts") or []):
    names.add(normalize_title(name))
names.discard("")
print(len(names), "unique hosts")

podcast_people = sc.broadcast(names)


records = (sc.newAPIHadoopFile(
    "file://" + os.path.abspath(WIKI_DUMP),
    "org.apache.hadoop.mapreduce.lib.input.TextInputFormat",
    "org.apache.hadoop.io.LongWritable",
    "org.apache.hadoop.io.Text",
    conf={"textinputformat.record.delimiter": "</page>"})
  .map(lambda pair: pair[1]))

 
shutil.rmtree(SPARK_WIKI_DIR, ignore_errors=True)

(records
   .map(extract_wiki)
   .filter(lambda line: line is not None)
   .saveAsTextFile(SPARK_WIKI_DIR))

extracted = sc.textFile(SPARK_WIKI_DIR)
print(extracted.count(), "wiki pages extracted")
print(extracted.map(lambda line: json.loads(line)["type"]).countByValue())
 
spark.stop()

with open(SPARK_WIKI_FILE, "wb") as out:
  for part in sorted(glob.glob(f"{SPARK_WIKI_DIR}/part-*")):
    with open(part, "rb") as f:
      shutil.copyfileobj(f, out)
 
shutil.rmtree(SPARK_WIKI_DIR)

