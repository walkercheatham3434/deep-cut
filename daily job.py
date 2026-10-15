#!/usr/bin/env python3
"""Deep Cut daily job. Runs on a schedule (GitHub Actions), no computer needed.

1. Pulls NBA award winners from Wikidata (CC0).
2. Builds candidate questions and merges them with the verified base bank.
3. Writes daily/YYYY-MM-DD.json for the next DAYS days, never repeating a
   question on back-to-back days.
4. If Wikidata fails or a list looks wrong, it keeps the previous daily files
   and exits 0, so the game never gets an empty or broken day.
"""
import datetime, hashlib, json, os, random, sys, urllib.parse, urllib.request
from collections import Counter

DAYS = 30
OUT = "daily"
BASE = "bank.json"            # verified base bank, same format as the game's
ENDPOINT = "https://query.wikidata.org/sparql"
UA = "DeepCutDailyJob/1.0 (free, non-commercial)"

AWARDS = {
    "NBA Most Valuable Player Award": ("wd-mvp", "AWARDS", "Name a player who has won NBA MVP.", "Every winner counts."),
    "NBA Defensive Player of the Year Award": ("wd-dpoy", "AWARDS", "Name a player who has won Defensive Player of the Year.", "Every winner since the award began in 1983."),
    "NBA Rookie of the Year Award": ("wd-roy", "AWARDS", "Name a player who has won Rookie of the Year.", "Co-winners count."),
    "NBA Finals Most Valuable Player Award": ("wd-fmvp", "AWARDS", "Name a player who has won Finals MVP.", "Every winner since 1969."),
    "NBA Sixth Man of the Year Award": ("wd-sixth", "BENCH MOB", "Name a player who has won Sixth Man of the Year.", "Every winner since 1983."),
    "NBA Most Improved Player Award": ("wd-mip", "BENCH MOB", "Name a player who has won Most Improved Player.", "Every winner since 1986."),
}
MIN_NAMES = 7       # a list with fewer names is skipped
MAX_SHRINK = 0.5    # if a list suddenly loses over half its names vs last run, keep the old one

def sparql_names(award_label):
    q = f'''SELECT DISTINCT ?personLabel WHERE {{
      ?award rdfs:label "{award_label}"@en .
      ?person wdt:P166 ?award ; wdt:P31 wd:Q5 .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}'''
    data = urllib.parse.urlencode({"query": q, "format": "json"}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = json.load(r)["results"]["bindings"]
    return sorted({r["personLabel"]["value"] for r in rows})

def tiers(n):
    return [1 + (i*5)//(n-1) for i in range(n-1)] + [6]

def question_from_names(qid, cat, prompt, hint, names):
    ts = tiers(len(names))
    return {"id": qid, "category": cat, "prompt": prompt, "hint": hint,
            "answers": [[n, t, []] for n, t in zip(names, ts)], "source": "Wikidata (CC0)"}

def valid(q):
    ts = [a[1] for a in q["answers"]]
    return all(t in ts for t in range(1, 7)) and ts.count(6) == 1 and len({a[0].lower() for a in q["answers"]}) == len(ts)

def load_base():
    if not os.path.exists(BASE): return []
    return [q for q in json.load(open(BASE, encoding="utf8"))["questions"] if valid(q) and not q.get("pending")]

def previous_counts():
    path = os.path.join(OUT, "_counts.json")
    return json.load(open(path)) if os.path.exists(path) else {}

def pull_wikidata(prev):
    fresh, counts = [], {}
    for label, (qid, cat, prompt, hint) in AWARDS.items():
        try:
            names = sparql_names(label)
        except Exception as e:
            print(f"wikidata failed for {label}: {e}", file=sys.stderr); continue
        counts[qid] = len(names)
        if len(names) < MIN_NAMES:
            print(f"skip {qid}: {len(names)} names", file=sys.stderr); continue
        old = prev.get(qid, 0)
        if old and len(names) < old * MAX_SHRINK:
            print(f"skip {qid}: {len(names)} names, was {old}; keeping old", file=sys.stderr); continue
        fresh.append(question_from_names(qid, cat, prompt, hint, names))
    return fresh, counts

def pick_seven(day_num, pool, prev_ids):
    """Deterministic daily seven. Avoids anything used yesterday when the pool allows."""
    seed = int(hashlib.sha256(f"day-{day_num}".encode()).hexdigest(), 16)
    rng = random.Random(seed)
    ordered = pool[:]
    rng.shuffle(ordered)
    fresh = [q for q in ordered if q["id"] not in prev_ids]
    rest = [q for q in ordered if q["id"] in prev_ids]
    seq = (fresh + rest)[:7]
    return seq

def main():
    os.makedirs(OUT, exist_ok=True)
    prev = previous_counts()
    base = load_base()
    try:
        wd, counts = pull_wikidata(prev)
    except Exception as e:
        print(f"wikidata unavailable, keeping existing daily files: {e}", file=sys.stderr)
        return 0
    if not wd:
        print("no award lists passed checks; keeping existing daily files", file=sys.stderr)
        return 0
    pool = base + wd
    if len(pool) < 7:
        print("pool too small, keeping existing daily files", file=sys.stderr)
        return 0
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("America/New_York")).date()  # must match the game's date
    prev_ids = set()
    for d in range(DAYS):
        day = today + datetime.timedelta(days=d)
        dn = (day - datetime.date(1970, 1, 1)).days
        qs = pick_seven(dn, pool, prev_ids)
        json.dump({"date": str(day), "questions": qs}, open(os.path.join(OUT, f"{day}.json"), "w"), ensure_ascii=False)
        prev_ids = {q["id"] for q in qs}
    json.dump({**prev, **{k: v for k, v in counts.items()}}, open(os.path.join(OUT, "_counts.json"), "w"))
    print(f"wrote {DAYS} days from a pool of {len(pool)} questions ({len(wd)} from Wikidata)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
