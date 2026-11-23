import re
import glob
import json
import shutil
from pyspark.sql import SparkSession
from wiki_parser import normalize_title

spark = (SparkSession.builder
         .master("local[8]")
         .appName("pages_join")
         .config("spark.driver.memory", "4g")
         .getOrCreate())
sc = spark.sparkContext

PODCASTS_FILE = "data/spark/podcasts.jsonl"
WIKI_FILE = "data/spark/wiki.jsonl"
JOINED_DIR = "data/spark/joined"
JOINED_FILE = "data/spark/podcasts_wiki.jsonl"

PERSON_FIELDS = ["birth_date", "birth_place", "occupation"]
PODCAST_FIELDS = ["genre", "began", "ended", "language"]

JOB_WORDS = ["host", "presenter", "broadcaster", "comedian", "journalist",
             "commentator", "podcaster", "radio", "television", "author", "actor"]


def wiki_text(wiki):
  text = wiki["summary"] + " " + " ".join(wiki["fields"].values())
  text = re.sub(r"[^\w\s]", "", (text or "").lower())
  return " " + re.sub(r"\s+", " ", text).strip() + " "

def mentions(name, text):
  if name and f" {name} " in text:
    return True
  return False

def pick(fields, names):
  return {name: fields[name] for name in names if name in fields}


podcasts = sc.textFile(PODCASTS_FILE).map(json.loads).cache()
wiki = sc.textFile(WIKI_FILE).map(json.loads)

wiki_podcasts = (wiki.filter(lambda w: w["type"] == "podcast")
                     .map(lambda w: (normalize_title(w["title"]), w)))
wiki_people = (wiki.filter(lambda w: w["type"] == "person")
                   .map(lambda w: (normalize_title(w["title"]), w)))



def title_candidates(podcast):
  title = normalize_title(podcast.get("title"))
  words = title.split()
  info = {
    "id": podcast["id"],
    "title": title,
    "hosts": podcast.get("hosts") or [],
    "publishers": [x for x in (podcast.get("channel"), podcast.get("provider")) if x],
  }

  result = []
  for i in range(len(words)):
    for j in range(i + 1, len(words) + 1):
      phrase = " ".join(words[i:j])
      result.append((phrase, info))
  return result


def score_podcast(pair):
  phrase, (info, wiki) = pair
  text = wiki_text(wiki)

  if phrase == info["title"]:
    score = 2
  else:
    score = 1

  for host in info["hosts"]:
    if mentions(normalize_title(host), text):
      score += 1
  for pub in info["publishers"]:
    if mentions(normalize_title(pub), text):
      score += 1

  return info["id"], (score, wiki)


podcast_matches = (podcasts.flatMap(title_candidates)
    .join(wiki_podcasts)
    .map(score_podcast)
    .filter(lambda t: t[1][0] >= 2)
    .reduceByKey(lambda a, b: a if a[0] >= b[0] else b)
    .collect())

podcast_wiki = {}
for podcast_id, (score, wiki) in podcast_matches:
  podcast_wiki[podcast_id] = {
    "wiki_id": wiki["wiki_id"],
    "wiki_title": wiki["title"],
    "summary": wiki["summary"],
    **pick(wiki["fields"], PODCAST_FIELDS),
  }



def person_keys(podcast):
  title = normalize_title(podcast.get("title"))
  result = []
  for name in podcast.get("hosts") or []:
    key = normalize_title(name)
    if key:
      result.append((key, (podcast["id"], name, title)))
  return result

def score_person(pair):
  key, ((podcast_id, name, podcast_title), wiki) = pair
  text = wiki_text(wiki)
  fields = wiki["fields"]
  occupation = fields.get("occupation", "").lower()

  score = 0
  if "podcast" in text:
    score += 1
  if mentions(podcast_title, text):
    score += 1
  for word in JOB_WORDS:
    if word in occupation:
      score += 1
  return (podcast_id, name), score, wiki


def best_per_host(candidates):
  candidates = list(candidates)
  best = max(candidates, key=lambda c: c[0])
  is_unique = len(candidates) == 1
  if is_unique or best[0] >= 2:
    return best
  return None

people_matches = (podcasts.flatMap(person_keys)
    .join(wiki_people)
    .map(score_person)
    .map(lambda t: (t[0], (t[1], t[2])))
    .groupByKey()
    .mapValues(best_per_host)
    .filter(lambda t: t[1] is not None)
    .collect())

people_wiki = {}
for (podcast_id, name), (score, wiki) in people_matches:
  entry = {
    "name": name,
    "wiki_id": wiki["wiki_id"],
    "wiki_title": wiki["title"],
    "summary": wiki["summary"],
    **pick(wiki["fields"], PERSON_FIELDS),
  }
  people_wiki.setdefault(podcast_id, {"hosts": []})["hosts"].append(entry)


podcast_wiki_b = sc.broadcast(podcast_wiki)
people_wiki_b = sc.broadcast(people_wiki)

def attach(podcast):
  podcast_id = podcast["id"]
  podcast["wiki"] = podcast_wiki_b.value.get(podcast_id)
  people = people_wiki_b.value.get(podcast_id, {"hosts": []})
  podcast["hosts_wiki"] = people["hosts"]
  return json.dumps(podcast, ensure_ascii=False)


shutil.rmtree(JOINED_DIR, ignore_errors=True)
podcasts.map(attach).saveAsTextFile(JOINED_DIR)


wiki_ids = {v["wiki_id"] for v in podcast_wiki.values()}
for roles in people_wiki.values():
  for entries in roles.values():
    wiki_ids.update(e["wiki_id"] for e in entries)

print("podcasts total:", podcasts.count())
print("podcasts joined with wiki podcast page:", len(podcast_wiki))
print("podcasts with at least one wiki host:", len(people_wiki))
print("unique wiki pages joined:", len(wiki_ids))

spark.stop()

with open(JOINED_FILE, "wb") as out:
  for part in sorted(glob.glob(f"{JOINED_DIR}/part-*")):
    with open(part, "rb") as f:
      shutil.copyfileobj(f, out)

shutil.rmtree(JOINED_DIR)