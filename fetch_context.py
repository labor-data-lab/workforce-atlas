"""
Download labor-market context for Workforce Atlas and save it to context.js.

Adds, for every state:
  - BLS: unemployment rate (LAUS) and job openings, hires and quits rates (JOLTS)
  - Census ACS 1-year: population, median household income, labor force participation, commuting
  - BEA: regional price parities (cost of living, U.S. = 100)
  - LEHD LODES (the data behind OnTheMap): where workers live versus where they work

Usage (only the Census key is required; the others turn on more data):
    python fetch_context.py --census CENSUS_KEY --bea BEA_KEY --bls BLS_KEY

    Census key: https://api.census.gov/data/key_signup.html
    BEA key:    https://apps.bea.gov/api/signup/   (needed for cost of living)
    BLS key:    https://data.bls.gov/registrationEngine/   (optional; without it BLS allows 25 requests a day)

    --skip-lodes     skip the commuting files (about 100 downloads, a few minutes)
    --county-flows   also build county-to-county commuting (large download, roughly 1 GB, 20-60 minutes)

Run it again whenever you want fresher numbers. Standard library only, Python 3.8+.
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import urllib.error
import urllib.request

UA = {"User-Agent": "WorkforceAtlas/1.0 (data download script)"}
STATES = {"01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca", "08": "co", "09": "ct", "10": "de",
          "11": "dc", "12": "fl", "13": "ga", "15": "hi", "16": "id", "17": "il", "18": "in", "19": "ia",
          "20": "ks", "21": "ky", "22": "la", "23": "me", "24": "md", "25": "ma", "26": "mi", "27": "mn",
          "28": "ms", "29": "mo", "30": "mt", "31": "ne", "32": "nv", "33": "nh", "34": "nj", "35": "nm",
          "36": "ny", "37": "nc", "38": "nd", "39": "oh", "40": "ok", "41": "or", "42": "pa", "44": "ri",
          "45": "sc", "46": "sd", "47": "tn", "48": "tx", "49": "ut", "50": "vt", "51": "va", "53": "wa",
          "54": "wv", "55": "wi", "56": "wy"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def get(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=dict(UA, **({"Content-Type": "application/json"} if data else {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def num(x):
    try:
        v = float(x)
        return v if v >= 0 else None  # ACS uses large negative codes for "not available"
    except (TypeError, ValueError):
        return None


# ---------------- ACS ----------------
ACS_CORE = ("NAME,B01003_001E,B19013_001E,B01002_001E,B08013_001E,B08303_001E,B08301_001E,"
            "B08301_003E,B08301_010E,B08301_021E,B23025_001E,B23025_002E,B20002_001E,B20002_002E,B20002_003E")
ACS_POW = "B08007_001E,B08007_005E"


def acs_table(y, get_vars, forv, key):
    try:
        body = get(f"https://api.census.gov/data/{y}/acs/acs1?get={get_vars}&for={forv}&key={key}")
        t = json.loads(body)
        return [dict(zip(t[0], r)) for r in t[1:]]
    except Exception:
        return None


def acs_rows(core, pow_):
    pm = {r.get("state", "US"): r for r in (pow_ or [])}
    out = {}
    for r in core or []:
        g = r.get("state", "US")
        n = lambda k: num(r.get(k))
        p = lambda k: num(pm.get(g, {}).get(k))
        d = lambda a, b: a / b if a is not None and b else None
        out[g] = {"med": n("B20002_001E"), "medM": n("B20002_002E"), "medF": n("B20002_003E"), "pop": n("B01003_001E"), "mhi": n("B19013_001E"), "medAge": n("B01002_001E"),
                  "commute": d(n("B08013_001E"), n("B08303_001E")),
                  "wfh": d(n("B08301_021E"), n("B08301_001E")),
                  "drive": d(n("B08301_003E"), n("B08301_001E")),
                  "transit": d(n("B08301_010E"), n("B08301_001E")),
                  "lfpr": d(n("B23025_002E"), n("B23025_001E")),
                  "outState": d(p("B08007_005E"), p("B08007_001E"))}
    return out


def fetch_acs5_counties(key):
    """County names plus population, income and commuting (ACS 5-year covers every county)."""
    y0 = dt.date.today().year - 1
    for y in range(y0, y0 - 5, -1):
        try:
            t = json.loads(get(f"https://api.census.gov/data/{y}/acs/acs5?get={ACS_CORE}&for=county:*&key={key}"))
        except Exception:
            continue
        out = {}
        for row in t[1:]:
            r = dict(zip(t[0], row))
            if r["state"] not in STATES:
                continue
            n = lambda k: num(r.get(k))
            d = lambda a, b: a / b if a is not None and b else None
            out[r["state"] + r["county"]] = {
                "name": r["NAME"].split(",")[0], "med": n("B20002_001E"), "medM": n("B20002_002E"), "medF": n("B20002_003E"), "pop": n("B01003_001E"), "mhi": n("B19013_001E"),
                "medAge": n("B01002_001E"), "commute": d(n("B08013_001E"), n("B08303_001E")),
                "wfh": d(n("B08301_021E"), n("B08301_001E")), "drive": d(n("B08301_003E"), n("B08301_001E")),
                "transit": d(n("B08301_010E"), n("B08301_001E")), "lfpr": d(n("B23025_002E"), n("B23025_001E"))}
        print(f"ACS 5-year {y}: {len(out)} counties")
        return {"year": y, "counties": out}
    print("ACS 5-year: no data found")
    return None


def fetch_acs(key):
    y0 = dt.date.today().year - 1
    for y in range(y0, y0 - 6, -1):
        if y == 2020:  # no standard 1-year release for 2020
            continue
        core = acs_table(y, ACS_CORE, "state:*", key)
        if not core:
            continue
        st = acs_rows(core, acs_table(y, ACS_POW, "state:*", key))
        us = acs_rows(acs_table(y, ACS_CORE, "us:1", key), acs_table(y, ACS_POW, "us:1", key))
        print(f"ACS {y}: {len(st)} states")
        return {"year": y, "states": st, "us": us.get("US", {})}
    print("ACS: no data found")
    return None


# ---------------- BLS ----------------
def bls_series(ids, key):
    out = {}
    size = 50 if key else 25
    url = "https://api.bls.gov/publicAPI/" + ("v2" if key else "v1") + "/timeseries/data/"
    y = dt.date.today().year
    for i in range(0, len(ids), size):
        payload = {"seriesid": ids[i:i + size], "startyear": str(y - 2), "endyear": str(y)}
        if key:
            payload["registrationkey"] = key
        res = json.loads(get(url, json.dumps(payload).encode()))
        if res.get("status") != "REQUEST_SUCCEEDED":
            print("  BLS:", "; ".join(res.get("message", []))[:300])
        for s in res.get("Results", {}).get("series", []):
            pts = [p for p in s.get("data", []) if p["period"].startswith("M") and p["period"] != "M13"]
            if not pts:
                continue
            pts.sort(key=lambda p: (p["year"], p["period"]), reverse=True)
            last = pts[0]
            prev = next((p for p in pts if p["year"] == str(int(last["year"]) - 1) and p["period"] == last["period"]), None)
            out[s["seriesID"]] = {"v": num(last["value"]), "yr": num(prev["value"]) if prev else None,
                                  "per": f'{MONTHS[int(last["period"][1:]) - 1]} {last["year"]}'}
    return out


def fetch_bls(key):
    ids = {"US": {"ur": "LNS14000000", "jor": "JTS000000000000000JOR", "qur": "JTS000000000000000QUR",
                  "hir": "JTS000000000000000HIR"}}
    for f in STATES:
        ids[f] = {"ur": f"LASST{f}0000000000003", "jor": f"JTS000000{f}0000000JOR",
                  "qur": f"JTS000000{f}0000000QUR", "hir": f"JTS000000{f}0000000HIR"}
    flat = [sid for d in ids.values() for sid in d.values()]
    data = bls_series(flat, key)
    if not data:
        print("BLS: no data returned")
        return None
    res = {"states": {}, "us": {}}
    lab = {}
    for g, d in ids.items():
        o = {}
        for k, sid in d.items():
            x = data.get(sid)
            if x:
                o[k] = x["v"]
                o[k + "Yr"] = x["yr"]
                lab.setdefault(k, x["per"])
        if g == "US":
            res["us"] = o
        else:
            res["states"][g] = o
    res["label"] = lab.get("ur", "")
    res["labelJ"] = lab.get("jor", "")
    print(f"BLS: unemployment {res['label']}, JOLTS {res['labelJ']}")
    return res


def fetch_bls_counties(key, county_list=None):
    """County unemployment rates (BLS LAUS, not seasonally adjusted). Tries BLS's one-file download first."""
    lines = None
    for url in ("https://www.bls.gov/web/metro/laucntycur14.txt",):
        try:
            lines = get(url).decode("latin-1").splitlines()
            break
        except Exception as e:
            print(f"  BLS county file not available ({e}); trying the API")
    rows = {}
    if lines:
        for ln in lines:
            f = [x.strip() for x in ln.split("|")]
            if len(f) < 9 or not f[1].isdigit() or not f[2].isdigit():
                continue
            g = f[1].zfill(2) + f[2].zfill(3)
            per = f[4].replace("(p)", "").strip()  # e.g. "Sep-25"
            try:
                m, yy = per.split("-")
                ym = (2000 + int(yy), MONTHS.index(m[:3]) + 1)
            except ValueError:
                continue
            rows.setdefault(g, {})[ym] = {"ur": num(f[8].replace(",", "")), "lf": num(f[5].replace(",", "")),
                                          "unemp": num(f[7].replace(",", ""))}
    elif key:
        ids = {}
        gs = list(county_list or [])
        if not gs:
            try:
                t = json.loads(get("https://api.census.gov/data/2020/dec/pl?get=NAME&for=county:*"))  # county list
                gs = [r[1] + r[2] for r in t[1:] if r[1] in STATES]
            except Exception:
                gs = []
        print(f"  BLS API: requesting {len(gs)} county series in batches of 50...")
        for g in gs:
            ids[f"LAUCN{g}0000000003"] = g
        data = bls_series(list(ids), key)
        for sid, x in data.items():
            m, y = x["per"].split()
            ym = (int(y), MONTHS.index(m) + 1)
            rows.setdefault(ids[sid], {})[ym] = {"ur": x["v"]}
            if x["yr"] is not None:
                rows[ids[sid]][(ym[0] - 1, ym[1])] = {"ur": x["yr"]}
    else:
        print("BLS counties: skipped (the BLS file wasn't reachable; add --bls KEY to use the API instead)")
        return None
    if not rows:
        print("BLS counties: no data")
        return None
    latest = max(ym for r in rows.values() for ym in r)
    out = {}
    for g, r in rows.items():
        cur, prev = r.get(latest), r.get((latest[0] - 1, latest[1]))
        if cur and cur.get("ur") is not None:
            out[g] = {"ur": cur["ur"], "urYr": prev["ur"] if prev else None, "lf": cur.get("lf"), "unemp": cur.get("unemp")}
    label = f"{MONTHS[latest[1] - 1]} {latest[0]}"
    print(f"BLS counties {label}: {len(out)} counties")
    return {"label": label, "counties": out}


# ---------------- BEA ----------------
def fetch_bea(key):
    url = ("https://apps.bea.gov/api/data?UserID=" + key + "&method=GetData&datasetname=Regional"
           "&TableName=SARPP&LineCode=1&GeoFips=STATE&Year=LAST5&ResultFormat=json")
    res = json.loads(get(url))
    rows = res.get("BEAAPI", {}).get("Results", {}).get("Data", [])
    if not rows:
        print("BEA:", json.dumps(res)[:300])
        return None
    year = max(r["TimePeriod"] for r in rows)
    st = {}
    for r in rows:
        f = r["GeoFips"][:2]
        if r["TimePeriod"] == year and f in STATES:
            st[f] = {"rpp": num(str(r["DataValue"]).replace(",", ""))}
    print(f"BEA price parities {year}: {len(st)} states")
    return {"year": year, "states": st}


# ---------------- LODES ----------------
LODES = "https://lehd.ces.census.gov/data/lodes/LODES8/"


def gz_csv(url):
    raw = get(url, timeout=600)
    return csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"))


def gz_rows(url):
    """Stream a large .csv.gz without holding it in memory."""
    req = urllib.request.Request(url, headers=UA)
    resp = urllib.request.urlopen(req, timeout=600)
    return csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=resp), encoding="utf-8"))


def fetch_lodes_counties(year):
    """County-to-county commuting from the full origin-destination files (large: roughly 1 GB in total)."""
    pairs = {}  # (home county, work county) -> jobs
    for f, ab in STATES.items():
        for part in ("main", "aux"):
            # count into a scratch table and keep it only if the whole file was read;
            # a dropped connection used to leave partial counts (found in testing: Santa Clara County)
            for attempt in range(3):
                part_pairs, n = {}, 0
                try:
                    for r in gz_rows(f"{LODES}{ab}/od/{ab}_od_{part}_JT00_{year}.csv.gz"):
                        k = (r["h_geocode"][:5], r["w_geocode"][:5])
                        part_pairs[k] = part_pairs.get(k, 0) + int(r["S000"])
                        n += 1
                    for k, c in part_pairs.items():
                        pairs[k] = pairs.get(k, 0) + c
                    print(f"  {ab} {part}: {n:,} rows")
                    break
                except Exception as e:
                    print(f"  {ab} {part}: attempt {attempt + 1} failed after {n:,} rows ({e})")
            else:
                print(f"  {ab} {part}: skipped after 3 attempts")
    jobs, res, same, frm, to = {}, {}, {}, {}, {}
    for (h, w), c in pairs.items():
        jobs[w] = jobs.get(w, 0) + c
        res[h] = res.get(h, 0) + c
        if h == w:
            same[w] = same.get(w, 0) + c
        else:
            frm.setdefault(w, []).append((h, c))
            to.setdefault(h, []).append((w, c))
    out = {}
    for g in set(jobs) | set(res):
        if g[:2] not in STATES:
            continue
        out[g] = {"jobs": jobs.get(g, 0), "res": res.get(g, 0), "same": same.get(g, 0),
                  "from": [[h, c] for h, c in sorted(frm.get(g, []), key=lambda x: -x[1])[:6]],
                  "to": [[w, c] for w, c in sorted(to.get(g, []), key=lambda x: -x[1])[:6]]}
    print(f"LODES counties {year}: {len(out)} counties")
    return {"year": year, "counties": out}


# Worker profile columns kept from LODES WAC/RAC files (age, earnings, industry, race, ethnicity, education, sex)
LCOLS = (["C000", "CA01", "CA02", "CA03", "CE01", "CE02", "CE03"] + [f"CNS{i:02d}" for i in range(1, 21)] +
         ["CR01", "CR02", "CR03", "CR04", "CR05", "CR07", "CT01", "CT02", "CD01", "CD02", "CD03", "CD04", "CS01", "CS02"])
DETAILS = {}  # written to details.js (loaded by the site only when a page needs it)


def lodes_profile(rows, geo_col, into):
    for r in rows:
        g = r[geo_col][:5]
        acc = into.setdefault(g, [0] * len(LCOLS))
        for i, c in enumerate(LCOLS):
            v = r.get(c)
            if v:
                acc[i] += int(v)


def fetch_lodes():
    year = None
    for y in range(dt.date.today().year - 1, dt.date.today().year - 8, -1):
        try:
            get(f"{LODES}ri/od/ri_od_aux_JT00_{y}.csv.gz", timeout=60)
            year = y
            break
        except urllib.error.HTTPError:
            continue
    if not year:
        print("LODES: no recent files found")
        return None
    print(f"LODES {year}: downloading commuting files for each state...")
    jobs, inc, flows = {}, {}, {}  # flows[(home, work)] = workers
    wac, rac = {}, {}
    for f, ab in STATES.items():
        try:
            part = {}
            lodes_profile(gz_csv(f"{LODES}{ab}/wac/{ab}_wac_S000_JT00_{year}.csv.gz"), "w_geocode", part)
            wac.update(part)
            jobs[f] = sum(v[0] for v in part.values())
            try:
                lodes_profile(gz_csv(f"{LODES}{ab}/rac/{ab}_rac_S000_JT00_{year}.csv.gz"), "h_geocode", rac)
            except Exception as e:
                print(f"  {ab}: residence profile skipped ({e})")
            n = 0
            for r in gz_csv(f"{LODES}{ab}/od/{ab}_od_aux_JT00_{year}.csv.gz"):
                h = r["h_geocode"][:2]
                c = int(r["S000"])
                n += c
                flows[(h, f)] = flows.get((h, f), 0) + c
            inc[f] = n
            print(f"  {ab}: {jobs[f]:,} jobs, {n:,} held by out-of-state residents")
        except Exception as e:
            print(f"  {ab}: skipped ({e})")
    st = {}
    for f in jobs:
        frm = sorted(((h, c) for (h, w), c in flows.items() if w == f and h in STATES), key=lambda x: -x[1])
        to = sorted(((w, c) for (h, w), c in flows.items() if h == f), key=lambda x: -x[1])
        st[f] = {"jobs": jobs[f], "inc": inc[f], "out": sum(c for _, c in to),
                 "from": [[h, c] for h, c in frm[:6]], "to": [[w, c] for w, c in to[:6]]}
    sum_by_state = lambda d: {f: [sum(v[i] for g, v in d.items() if g[:2] == f) for i in range(len(LCOLS))] for f in STATES if any(g[:2] == f for g in d)}
    DETAILS["lodesW"] = {"year": year, "cols": LCOLS, "wac": wac, "rac": rac, "wacS": sum_by_state(wac), "racS": sum_by_state(rac)}
    print(f"LODES worker profiles {year}: {len(wac)} counties (work), {len(rac)} counties (home)")
    return {"year": year, "states": st}


# ---------------- BLS QCEW: employers, jobs and pay by industry ----------------
QCEW_IND = ["10", "11", "21", "22", "23", "31-33", "42", "44-45", "48-49", "51", "52", "53", "54", "55", "56",
            "61", "62", "71", "72", "81"]


def qcew_ua(contact):
    ua = dict(UA)
    if contact:
        ua["User-Agent"] = f"WorkforceAtlas/1.0 (contact: {contact})"
    return ua


def qcew_csv(url, ua):
    req = urllib.request.Request(url, headers=ua)
    with urllib.request.urlopen(req, timeout=300) as r:
        return list(csv.DictReader(io.TextIOWrapper(r, encoding="utf-8")))


def qcew_year(year, ua, rows10=None):
    """Private-sector establishments, jobs and average annual pay for one year, by county and state."""
    counties, states = {}, {}
    def take(rows, code):
        for r in rows:
            if r.get("own_code") != "5" or r.get("industry_code", "").replace("_", "-") != code:
                continue
            a = r.get("area_fips", "")
            if len(a) != 5 or not a.isdigit() or a[:2] not in STATES:
                continue
            if r.get("disclosure_code") == "N":
                vals = [None, None, None]
            else:
                vals = [num(r.get("annual_avg_estabs")), num(r.get("annual_avg_emplvl")), num(r.get("avg_annual_pay"))]
            (states if a.endswith("000") else counties).setdefault(a[:2] if a.endswith("000") else a, {})[code] = vals
    take(rows10 if rows10 is not None else qcew_csv(f"https://data.bls.gov/cew/data/api/{year}/a/industry/10.csv", ua), "10")
    for code in QCEW_IND[1:]:
        try:
            take(qcew_csv(f"https://data.bls.gov/cew/data/api/{year}/a/industry/{code.replace('-', '_')}.csv", ua), code)
        except Exception as e:
            print(f"  QCEW {year} {code}: skipped ({e})")
    return {"year": year, "cols": ["estabs", "jobs", "avgPay"], "counties": counties, "states": states}


def fetch_qcew(contact):
    """Newest annual QCEW year, kept in details.js."""
    ua = qcew_ua(contact)
    for y in range(dt.date.today().year - 1, dt.date.today().year - 4, -1):
        try:
            rows10 = qcew_csv(f"https://data.bls.gov/cew/data/api/{y}/a/industry/10.csv", ua)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print("QCEW: BLS refused the download. Add --contact you@example.com (BLS asks scripts to identify themselves).")
                return None
            continue
        if rows10:
            d = qcew_year(y, ua, rows10)
            DETAILS["qcew"] = d
            print(f"QCEW {y}: {len(d['counties'])} counties, {len(d['states'])} states")
            return {"year": y}
    print("QCEW: no annual data found")
    return None


def lodes_profile_year(year):
    """Worker profiles by county (work place and home) for one year; used for history files."""
    wac, rac = {}, {}
    for f, ab in STATES.items():
        for kind, col, into in (("wac", "w_geocode", wac), ("rac", "h_geocode", rac)):
            try:
                lodes_profile(gz_csv(f"{LODES}{ab}/{kind}/{ab}_{kind}_S000_JT00_{year}.csv.gz"), col, into)
            except Exception as e:
                print(f"  LODES {year} {ab} {kind}: skipped ({e})")
    sum_by_state = lambda d: {f: [sum(v[i] for g, v in d.items() if g[:2] == f) for i in range(len(LCOLS))] for f in STATES if any(g[:2] == f for g in d)}
    return {"year": year, "cols": LCOLS, "wac": wac, "rac": rac, "wacS": sum_by_state(wac), "racS": sum_by_state(rac)}


HIST_YEARS = 10  # past years of LEHD profiles and QCEW kept as details/<kind>_<year>.js


def write_detail_history(here, contact):
    """Save older years once; later runs only add years that are missing."""
    hdir = os.path.join(here, "details")
    os.makedirs(hdir, exist_ok=True)
    def save(kind, data):
        with open(os.path.join(hdir, f"{kind}_{data['year']}.js"), "w", encoding="utf-8") as fh:
            fh.write(f"(window.CTX2H=window.CTX2H||{{}})['{kind}_{data['year']}']=" + json.dumps(data, separators=(",", ":")) + ";\n")
    have = lambda kind, y: os.path.exists(os.path.join(hdir, f"{kind}_{y}.js"))
    if "lodesW" in DETAILS:
        last = DETAILS["lodesW"]["year"]
        for y in range(last - 1, last - HIST_YEARS, -1):
            if not have("lodes", y):
                print(f"LODES history {y}...")
                d = lodes_profile_year(y)
                if d["wac"]:
                    save("lodes", d)  # years with no published files are skipped
    if "qcew" in DETAILS:
        last, ua = DETAILS["qcew"]["year"], qcew_ua(contact)
        for y in range(last - 1, last - HIST_YEARS, -1):
            if not have("qcew", y):
                print(f"QCEW history {y}...")
                try:
                    d = qcew_year(y, ua)
                    if d["counties"]:
                        save("qcew", d)
                except Exception as e:
                    print(f"  QCEW {y}: skipped ({e})")
    years = lambda kind: sorted(int(n.split("_")[1][:4]) for n in os.listdir(hdir) if n.startswith(kind + "_"))
    DETAILS["hist"] = {"lodes": years("lodes"), "qcew": years("qcew")}
    print("Detail history:", DETAILS["hist"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--census", default=os.environ.get("CENSUS_API_KEY", ""))
    ap.add_argument("--bea", default=os.environ.get("BEA_API_KEY", ""))
    ap.add_argument("--bls", default=os.environ.get("BLS_API_KEY", ""))
    ap.add_argument("--skip-lodes", action="store_true")
    ap.add_argument("--contact", default=os.environ.get("BLS_CONTACT", ""), help="your email, sent to BLS when downloading QCEW files")
    ap.add_argument("--county-flows", action="store_true", help="also download county-to-county commuting (about 1 GB, 20-60 minutes)")
    a = ap.parse_args()
    ctx = {"fetched": dt.date.today().isoformat()}
    steps = [("acs", lambda: fetch_acs(a.census) if a.census else print("ACS: skipped (no --census key)")),
             ("acs5", lambda: fetch_acs5_counties(a.census) if a.census else None),
             ("bls", lambda: fetch_bls(a.bls)),
             ("bea", lambda: fetch_bea(a.bea) if a.bea else print("BEA: skipped (no --bea key)")),
             ("blsC", lambda: fetch_bls_counties(a.bls, list(ctx.get("acs5", {}).get("counties", {})))),
             ("lodes", lambda: None if a.skip_lodes else fetch_lodes()),
             ("qcew", lambda: fetch_qcew(a.contact))]
    if a.county_flows and not a.skip_lodes:
        steps.append(("lodesC", lambda: fetch_lodes_counties(ctx["lodes"]["year"]) if "lodes" in ctx else print("LODES counties: skipped (state files failed)")))
    for name, fn in steps:
        try:
            r = fn()
            if r:
                ctx[name] = r
        except Exception as e:
            print(f"{name.upper()} failed: {e}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context.js")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("window.CTX=" + json.dumps(ctx, separators=(",", ":")) + ";\n")
    print("Saved", out, "with:", ", ".join(k for k in ctx if k != "fetched") or "nothing")
    try:
        write_detail_history(os.path.dirname(os.path.abspath(__file__)), a.contact)
    except Exception as e:
        print("Detail history failed:", e)
    det = os.path.join(os.path.dirname(os.path.abspath(__file__)), "details.js")
    with open(det, "w", encoding="utf-8") as fh:
        fh.write("window.CTX2=" + json.dumps(dict(DETAILS, fetched=ctx["fetched"]), separators=(",", ":")) + ";\n")
    print("Saved", det, "with:", ", ".join(DETAILS) or "nothing", f"({os.path.getsize(det) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
