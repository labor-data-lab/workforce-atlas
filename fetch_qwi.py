"""
Download Census QWI data for Workforce Atlas and save it to data.js.

Once data.js sits next to index.html, the site opens without an API key
(and you can host it publicly without exposing your key).

Usage:
    python fetch_qwi.py YOUR_CENSUS_API_KEY
    python fetch_qwi.py YOUR_CENSUS_API_KEY --counties   # also save county data (counties/XX.js)
    # or set the CENSUS_API_KEY environment variable and run: python fetch_qwi.py

Run it once a quarter (Windows Task Scheduler, cron, or GitHub Actions) to keep the site current.
Uses only the Python standard library. Python 3.8+.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.census.gov/data/timeseries/qwi/"
VARS = "Emp,EmpS,EarnS,HirA,Sep,TurnOvrS,FrmJbC"
DEMO_VARS = "Emp,EmpS,EarnS,HirA,TurnOvrS"
SECTORS = ["11", "21", "22", "23", "31-33", "42", "44-45", "48-49", "51", "52", "53",
           "54", "55", "56", "61", "62", "71", "72", "81"]
STATES = ["01", "02", "04", "05", "06", "08", "09", "10", "11", "12", "13", "15", "16", "17",
          "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31",
          "32", "33", "34", "35", "36", "37", "38", "39", "40", "41", "42", "44", "45", "46",
          "47", "48", "49", "50", "51", "53", "54", "55", "56"]
AGES = ["A00", "A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08"]
EDUS = ["E0", "E1", "E2", "E3", "E4", "E5"]

CACHE = {}


# Must build parameters in exactly the same order as bp() in index.html.
def bp(get, forv, y, qs, extra=()):
    p = [("get", get), ("for", forv), ("year", str(y))]
    p += [("quarter", str(q)) for q in qs]
    p.append(("ownercode", "A05"))
    p += list(extra)
    return p


def qkey(ds, p):
    return ds + "?" + "&".join(f"{k}={v}" for k, v in p)


def api(ds, params, key, tries=3):
    k = qkey(ds, params)
    if k in CACHE:
        return CACHE[k]
    url = BASE + k + "&key=" + key
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                body = r.read().decode("utf-8")
                table = json.loads(body) if r.status != 204 and body.strip() else []
                break
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (400, 404):
                print(f"  ! {e.code} for {k[:120]}: {msg}")
                table = []
                break
            if attempt == tries - 1:
                raise
        except json.JSONDecodeError:
            if "invalid key" in body.lower():
                sys.exit("The Census API did not accept this key. Check it and activate it from the email.")
            raise
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise
        time.sleep(2 * (attempt + 1))
    CACHE[k] = table
    return table


def rows(table):
    if not table or len(table) < 2:
        return []
    h = table[0]
    return [dict(zip(h, r)) for r in table[1:]]


def num(x):
    try:
        return None if x in (None, "") else float(x)
    except ValueError:
        return None


def tq(y, q):
    return f"{y}-Q{q}"


def tparse(t):
    return int(t[:4]), int(t[-1])


def tshift(t, n):
    y, q = tparse(t)
    i = y * 4 + (q - 1) + n
    return tq(i // 4, i % 4 + 1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_counties = "--counties" in sys.argv
    key = (args[0] if args else os.environ.get("CENSUS_API_KEY", "")).strip()
    if not key:
        sys.exit(__doc__)

    # 1. Newest quarter with data
    now = dt.date.today()
    t = tq(now.year, (now.month - 1) // 3 + 1)
    latest = None
    for _ in range(12):
        y, q = tparse(t)
        r = rows(api("sa", bp("Emp", "state:06", y, [q]), key))
        if any(num(x.get("Emp")) is not None for x in r):
            latest = t
            break
        t = tshift(t, -1)
    if not latest:
        sys.exit("Could not find recent QWI data.")
    print("Newest quarter:", latest)

    # 2. State totals and sectors for the last NQ quarters (index.html reads NQ from data.js)
    NQ = 80  # 20 years
    quarters = [tshift(latest, -i) for i in range(NQ - 1, -1, -1)]
    by_year = {}
    for t in quarters:
        y, q = tparse(t)
        by_year.setdefault(y, []).append(q)
    inds = [("industry", c) for c in SECTORS]
    base_counts = {}
    seen = set()
    def per_state(ds, get, y, qs, extra=()):
        # QWI does not accept state:* so request each state separately (same keys as index.html)
        with ThreadPoolExecutor(max_workers=6) as pool:
            tables = list(pool.map(lambda s: api(ds, bp(get, "state:" + s, y, qs, extra), key), STATES))
        return [r for t in tables for r in rows(t)]

    for y in sorted(by_year):
        print("Loading", y)
        tot = per_state("sa", VARS, y, by_year[y])
        per_state("sa", VARS, y, by_year[y], inds)
        for r in tot:
            seen.add(r["state"])
            tt = tq(r["year"], r["quarter"])
            for v in ("EarnS", "TurnOvrS"):
                if num(r.get(v)) is not None:
                    base_counts[(v, tt)] = base_counts.get((v, tt), 0) + 1

    need = -(-len(seen) * 9 // 10)  # newest quarter released by 90% of states; same rule as index.html

    def latest_for(v):
        for t in reversed(quarters):
            if base_counts.get((v, t), 0) >= need:
                return t
        return None

    lt, le = latest_for("TurnOvrS"), latest_for("EarnS")
    t_full = min(lt, le) if lt and le else (le or latest)
    print("Full-detail quarter:", t_full)
    y, q = tparse(t_full)

    # 3. Pay by sex and education, all states
    per_state("sa", "EarnS,EmpS", y, [q], [("sex", "1"), ("sex", "2")])
    per_state("se", "EarnS,EmpS", y, [q], [("education", "E2"), ("education", "E4")])

    # 4. Age / sex / education detail for each state (all industries, plus each sector)
    sexs = [("sex", "0"), ("sex", "1"), ("sex", "2")]
    jobs = []
    for s in STATES:
        for ind in ["00"] + SECTORS:
            ex = [] if ind == "00" else [("industry", ind)]
            jobs.append(("sa", bp(DEMO_VARS, "state:" + s, y, [q], sexs + [("agegrp", a) for a in AGES] + ex)))
            jobs.append(("se", bp(DEMO_VARS, "state:" + s, y, [q], sexs + [("education", e) for e in EDUS] + ex)))
    # 4b. Yearly views (all industries): pay by sex/education and "Who works here" for every saved year.
    #     Quarter lists follow index.html: each year's quarters up to the newest.
    for yy in sorted(by_year):
        qs = [x for x in (1, 2, 3, 4) if tq(yy, x) <= latest]
        jobs.append(("_all_sex", (yy, qs)))
        for s in STATES:
            jobs.append(("sa", bp(DEMO_VARS, "state:" + s, yy, qs, sexs + [("agegrp", a) for a in AGES])))
            jobs.append(("se", bp(DEMO_VARS, "state:" + s, yy, qs, sexs + [("education", e) for e in EDUS])))
    yearly = [j for j in jobs if j[0] == "_all_sex"]
    jobs = [j for j in jobs if j[0] != "_all_sex"]
    for _, (yy, qs) in yearly:
        per_state("sa", "EarnS,EmpS", yy, qs, [("sex", "1"), ("sex", "2")])
        per_state("se", "EarnS,EmpS", yy, qs, [("education", "E2"), ("education", "E4")])
    print(f"Loading demographic detail ({len(jobs)} requests)...")
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for _ in ex.map(lambda j: api(j[0], j[1], key), jobs):
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)}")

    split = int(tshift(latest, -11)[:4])  # data.js holds the years index.html opens with; older years go to history/YYYY.js
    meta = {"latest": latest, "first": min(by_year), "split": split, "fetched": dt.date.today().isoformat()}

    # 5. Optional: county totals and sectors, one file per state in counties/ (loaded when a state is opened)
    if with_counties:
        cdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "counties")
        os.makedirs(cdir, exist_ok=True)
        for s in STATES:
            part = {}
            # counties: the 3 newest years only, to keep each state's file small
            for y in [yy for yy in sorted(by_year) if yy >= int(latest[:4]) - 2]:
                for extra in ([], inds):
                    p = bp(VARS, "county:*&in=state:" + s, y, by_year[y], extra)
                    t = api("sa", p, key)
                    part[qkey("sa", p)] = t
                    CACHE.pop(qkey("sa", p), None)  # keep county tables out of data.js
            with open(os.path.join(cdir, s + ".js"), "w", encoding="utf-8") as f:
                f.write("Object.assign(window.QWI_CACHE=window.QWI_CACHE||{}," + json.dumps(part, separators=(",", ":")) + ");\n")
            print(f"  counties/{s}.js: {sum(max(0, len(t) - 1) for t in part.values()):,} rows")
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    hist = {}
    for k in list(CACHE):
        m = re.search(r"&year=(\d{4})", k)
        if m and int(m.group(1)) < split:
            hist.setdefault(int(m.group(1)), {})[k] = CACHE.pop(k)
    hdir = os.path.join(here, "history")
    os.makedirs(hdir, exist_ok=True)
    for yy, part in sorted(hist.items()):
        with open(os.path.join(hdir, f"{yy}.js"), "w", encoding="utf-8") as f:
            f.write("Object.assign(window.QWI_CACHE=window.QWI_CACHE||{}," + json.dumps(part, separators=(",", ":")) + ");\n")
    print(f"Saved {len(hist)} history files ({min(hist) if hist else '-'}-{max(hist) if hist else '-'}) in {hdir}")
    out = os.path.join(here, "data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.QWI_META=" + json.dumps(meta) + ";\n")
        f.write("window.QWI_CACHE=" + json.dumps(CACHE, separators=(",", ":")) + ";\n")
    print(f"Saved {len(CACHE)} queries to {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
