import glob
import shutil
import json
from pyspark.sql import SparkSession
from utils import read_tsv

spark = (SparkSession.builder
         .master("local[8]")
         .appName("pages_extract")
         .config("spark.driver.memory", "4g")
         .getOrCreate())
sc = spark.sparkContext

PAGES_FILE = "data/pages.tsv"
SPARK_PAGES_DIR = "data/spark/podcasts"
SPARK_PAGES_FILE = "data/spark/podcasts.jsonl"

def extract_page(row):
  import extractor
  from utils import podcast_id

  url, path, fetched_at = row
  try:
    with open(path, "r", encoding="utf-8") as file:
      page = file.read()
    podcast = {
      "id": podcast_id(url),
      "url": url,
      **extractor.extract_podcast(page),
      "episodes": extractor.extract_episodes(page, fetched_at),
    }
    return json.dumps(podcast, ensure_ascii=False)
  except Exception as e:
    print("error:", url, e)
    return None

queue = [(row["url"], row["file"].replace("\\", "/"), row["fetched_at"]) for row in read_tsv(PAGES_FILE) if row["http_code"] == "200" and row["file"]]


shutil.rmtree(SPARK_PAGES_DIR, ignore_errors=True)

(sc.parallelize(queue, 200)
   .map(extract_page)
   .filter(lambda line: line is not None)
   .saveAsTextFile(SPARK_PAGES_DIR))

print(len(queue), "pages,", sc.textFile(SPARK_PAGES_DIR).count(), "podcasts extracted")

spark.stop()

with open(SPARK_PAGES_FILE, "wb") as out:
  for part in sorted(glob.glob(f"{SPARK_PAGES_DIR}/part-*")):
    with open(part, "rb") as f:
      shutil.copyfileobj(f, out)

shutil.rmtree(SPARK_PAGES_DIR)