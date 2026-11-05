"""Check Greylock Techfair 2026 companies for new intern / new grad postings.

Usage (run with python -I):
  python -I greylock_jobs.py check  --state <dir>   # print new candidate postings as JSON, save snapshot to pending.json
  python -I greylock_jobs.py commit --state <dir>   # mark the pending snapshot as seen (run after the sheet is updated)
  python -I greylock_jobs.py baseline --state <dir> # check + commit silently (first-time setup)

"New" means the posting's key (its URL, or company+title for boards without per-job URLs)
was not on any board at a previous committed check. Seen keys are never removed, so a source
that fails once does not cause its old postings to reappear as new.
"""
import concurrent.futures as cf
import datetime as dt
import html as H
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}

ASHBY = {
    "OpenAI": "openai", "Harvey": "harvey", "Applied Intuition": "applied", "Baseten": "baseten",
    "Ramp": "ramp", "Cognition": "cognition", "Listen Labs": "listenlabs", "Netic": "netic",
    "Opal Security": "opal", "Cogent Security": "cogent-security", "Fable Security": "fable",
    "Lightfield": "Lightfield", "LlamaIndex": "llamaindex", "Mandolin": "mandolin", "Poetic": "poetic",
    "Cylake": "cylake-inc", "Long Lake": "long-lake",
}
GREENHOUSE = {"Cresta": "cresta", "Glean": "gleanwork", "Instawork": "instawork", "Nuro": "nuro", "Snorkel AI": "snorkelai"}
LEVER = {"Onehouse": "Onehouse"}
GEM = {"Altara": "altara"}

TITLE_RE = re.compile(r"\bintern(s|ship)?\b|\bco-?op\b|new\s*grad|new\s+graduate|recent\s+grad|university\s+grad|campus|"
                      r"emerging\s+talent|early\s+talent|early[\s-]career|entry[\s-]level|\bfellow(ship)?\b|\bstudent|apprentice", re.I)
TITLE_EXCLUDE = re.compile(r"president|resident solutions|openhouse", re.I)
US_RE = re.compile(r"united states|\bU\.?S\.?A?\b|\bUS\b|san francisco|sunnyvale|mountain view|palo alto|menlo park|"
                   r"san jose|santa clara|san diego|los angeles|seattle|new york|\bNYC\b|boston|austin|boulder|denver|"
                   r"chicago|atlanta|washington|miami|pittsburgh|redwood city|san mateo|oakland|berkeley|"
                   r", ?(CA|NY|WA|TX|MA|CO|IL|GA|PA|NJ|VA|FL|OR|NC|MI|AZ|UT|MN)\b|california|texas|massachusetts", re.I)
NON_US_RE = re.compile(r"canada|toronto|vancouver|montreal|india|bengaluru|bangalore|hyderabad|pune|london|\bUK\b|"
                       r"united kingdom|england|dublin|ireland|paris|france|berlin|munich|germany|amsterdam|netherlands|"
                       r"zurich|switzerland|stockholm|sweden|madrid|spain|milan|italy|tokyo|japan|seoul|korea|"
                       r"singapore|sydney|melbourne|australia|tel aviv|israel|riyadh|dubai|mexico|brazil|sao paulo|"
                       r"warsaw|poland|lisbon|portugal|taipei|hong kong|shanghai|beijing|china|manila|philippines", re.I)
DEGREE_RE = re.compile(r"ph\.?\s?d|doctoral|doctorate|postdoc|master'?s|\bM\.?S\.?c?\b|bachelor'?s|\bB\.?S\.?\b|\bB\.?A\.?\b|"
                       r"undergrad|graduate student|degree", re.I)


def get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def getj(url):
    return json.loads(get(url))


def clean(s):
    s = H.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def us_status(loc_text, countries=()):
    countries = [c for c in countries if c]
    if any(re.search(r"united states|^us$|^usa$", c, re.I) for c in countries):
        return "us"
    if US_RE.search(loc_text or ""):
        return "us"
    if countries or NON_US_RE.search(loc_text or ""):
        return "non-us"
    return "unknown"


def job(company, title, location, url, posted="", desc="", countries=(), key=None):
    title = re.sub(r"\s+", " ", title or "").strip()
    location = re.sub(r"\s+", " ", location or "").strip()
    return {"key": key or url, "company": company, "title": title, "location": location, "url": url,
            "posted": (posted or "")[:10], "us": us_status(location, countries), "desc": desc or ""}


def ashby(company, slug):
    d = getj(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""]
        countries = [((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location") or "")
            countries.append(((s.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        loc = locs[0] + (f" (+{', '.join(l for l in locs[1:] if l)})" if len(locs) > 1 else "")
        out.append(job(company, j.get("title"), loc, j.get("jobUrl"), j.get("publishedAt"), j.get("descriptionPlain"), countries))
    return out


def greenhouse(company, slug):
    d = getj(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = "; ".join(o.get("name", "") for o in j.get("offices") or [])
        out.append(job(company, j.get("title"), loc if not offices else f"{loc} [{offices}]", j.get("absolute_url"),
                       j.get("first_published") or j.get("updated_at"), clean(j.get("content"))))
    return out


def lever(company, slug):
    d = getj(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        loc = ", ".join(c.get("allLocations") or [c.get("location", "")])
        desc = (j.get("descriptionPlain") or "") + " " + " ".join(clean(l.get("content")) for l in j.get("lists") or [])
        posted = dt.datetime.fromtimestamp(j["createdAt"] / 1000, dt.timezone.utc).isoformat() if j.get("createdAt") else ""
        cc = j.get("country")
        out.append(job(company, j.get("text"), loc, j.get("hostedUrl"), posted, desc,
                       ["United States" if cc == "US" else cc] if cc else ()))
    return out


def gem(company, slug):
    d = getj(f"https://api.gem.com/job_board/v0/{slug}/job_posts/")
    out = []
    for j in d:
        loc = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location") or "")
        out.append(job(company, j.get("title"), loc, j.get("absolute_url"), j.get("first_published_at") or j.get("created_at"),
                       j.get("content_plain") or clean(j.get("content"))))
    return out


def zoho_axiamatic():
    h = H.unescape(get("https://axiamatic.zohorecruit.in/jobs/Careers"))
    out = []
    for m in re.finditer(r'\{[^{}]*"Posting_Title":"[^"]+"[^{}]*\}', h):
        try:
            o = json.loads(m.group(0))
        except Exception:
            continue
        title, jid = o.get("Posting_Title", ""), o.get("id", "")
        loc = ", ".join(x for x in [o.get("City"), o.get("State"), o.get("Country")] if x) or ("Remote" if o.get("Remote_Job") else "")
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
        out.append(job("Axiamatic", title, loc, f"https://axiamatic.zohorecruit.in/jobs/Careers/{jid}/{slug}",
                       o.get("Date_Opened") or "", clean(o.get("Job_Description")), [o.get("Country")] if o.get("Country") else ()))
    return out


def rippling_bedrock():
    h = get("https://ats.rippling.com/bedrock-data/jobs")
    out, seen = [], set()
    for href, txt in re.findall(r'<a[^>]+href="(/bedrock-data/jobs/[0-9a-f-]{36})"[^>]*>(.*?)</a>', h, re.S):
        t = clean(txt)
        if href in seen or not t:
            continue
        seen.add(href)
        out.append(job("Bedrock Data", t, "", "https://ats.rippling.com" + href))
    return out


def modular():
    page = "https://www.modular.com/company/careers"
    h = get(page)
    out = []
    for t in sorted(set(re.findall(r'"(?:title|name)"\s*:\s*"([^"]{4,100})"', h))):
        if re.search(r"engineer|intern|grad|scientist|designer|developer|manager|research|lead|analyst", t, re.I):
            out.append(job("Modular", t, "", page, key=f"Modular|{t}"))
    for u in sorted(set(re.findall(r"https://careers\.qualcomm\.com/careers/job/\d+", h))):
        try:
            title = clean((re.search(r"<title[^>]*>([^<]+)", get(u)) or [None, u])[1]).replace(" | Qualcomm", "")
        except Exception:
            title = "Qualcomm posting linked from Modular careers page"
        out.append(job("Modular", title, "Qualcomm US offices (see posting)", u))
    return out


def forge():
    page = "https://www.forge.ai/careers"
    h = get(page)
    out = []
    for txt in re.findall(r"<a[^>]*>(.*?)</a>", h, re.S):
        t = clean(txt)
        m = re.match(r"(.+?)\s+(San Francisco|New York|Remote|[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+Apply$", t)
        if m:
            out.append(job("Forge", m.group(1), m.group(2), page, key=f"Forge|{m.group(1)}"))
    return out


def locus():
    page = "https://paywithlocus.com/careers"
    t = clean(re.sub(r"<script.*?</script>|<style.*?</style>", " ", get(page), flags=re.S))
    out = []
    for m in re.finditer(r"([A-Z][A-Za-z/&\- ]{3,60}?)\s+(Full time|Part time|Intern(?:ship)?|Contract)\s+·\s+([A-Za-z ,]+?)\s+·", t):
        out.append(job("Locus", m.group(1).strip(), m.group(3).strip(), page, key=f"Locus|{m.group(1).strip()}"))
    return out


def superme():
    page = "https://www.superme.ai/careers"
    h = get(page).replace('\\"', '"')
    out = []
    for m in re.finditer(r'"job":\{"title":"([^"]+)","slug":"([^"]+)","location":"([^"]*)".*?"date":"([^"]*)"', h):
        out.append(job("SuperMe", m.group(1), m.group(3), f"{page}#{m.group(2)}", m.group(4), key=f"SuperMe|{m.group(2)}"))
    return out


def ahum():
    d = getj("https://www.ahum.ai/jobs/jobs.json")
    return [job("Ahum AI", j.get("title"), j.get("location") or "", "https://www.ahum.ai/careers",
                desc=j.get("description") or "", key=f"Ahum AI|{j.get('title')}") for j in d]


def sources():
    s = [(c, (lambda c=c, x=x: ashby(c, x))) for c, x in ASHBY.items()]
    s += [(c, (lambda c=c, x=x: greenhouse(c, x))) for c, x in GREENHOUSE.items()]
    s += [(c, (lambda c=c, x=x: lever(c, x))) for c, x in LEVER.items()]
    s += [(c, (lambda c=c, x=x: gem(c, x))) for c, x in GEM.items()]
    s += [("Axiamatic", zoho_axiamatic), ("Bedrock Data", rippling_bedrock), ("Modular", modular),
          ("Forge", forge), ("Locus", locus), ("SuperMe", superme), ("Ahum AI", ahum)]
    return s


def fetch_all():
    jobs, errors = [], {}

    def run(item):
        name, fn = item
        try:
            return name, fn(), None
        except Exception as e:
            return name, [], f"{type(e).__name__}: {str(e)[:120]}"

    with cf.ThreadPoolExecutor(10) as ex:
        for name, js, err in ex.map(run, sources()):
            if err:
                errors[name] = err
            jobs.extend(js)
    return jobs, errors


def degree_snippets(desc):
    t = re.sub(r"\s+", " ", desc or "")
    hits = []
    for m in DEGREE_RE.finditer(t):
        s = t[max(0, m.start() - 140):m.end() + 140]
        if not any(s[40:100] in h for h in hits):
            hits.append(s)
        if len(hits) >= 4:
            break
    return hits


def location_snippets(desc):
    t = re.sub(r"\s+", " ", desc or "")
    return [t[max(0, m.start() - 120):m.end() + 120] for m in re.finditer(r"remote|located|location|based in|time ?zone|relocat", t, re.I)][:3]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    state = sys.argv[sys.argv.index("--state") + 1] if "--state" in sys.argv else os.path.dirname(os.path.abspath(__file__))
    seen_path, pending_path = os.path.join(state, "seen.json"), os.path.join(state, "pending.json")
    seen = set(json.load(open(seen_path, encoding="utf-8"))) if os.path.exists(seen_path) else set()

    if cmd == "commit":
        pending = set(json.load(open(pending_path, encoding="utf-8"))) if os.path.exists(pending_path) else set()
        json.dump(sorted(seen | pending), open(seen_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(json.dumps({"committed": len(pending - seen), "seen_total": len(seen | pending)}))
        return

    jobs, errors = fetch_all()
    json.dump(sorted({j["key"] for j in jobs if j["key"]}), open(pending_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    if cmd == "baseline":
        json.dump(sorted(seen | {j["key"] for j in jobs if j["key"]}), open(seen_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(json.dumps({"baseline_jobs": len(jobs), "errors": errors}, ensure_ascii=False))
        return

    new = [j for j in jobs if j["key"] and j["key"] not in seen]
    candidates = []
    for j in new:
        if not TITLE_RE.search(j["title"]) or TITLE_EXCLUDE.search(j["title"]) or j["us"] == "non-us":
            continue
        c = {k: j[k] for k in ("company", "title", "location", "url", "posted", "us")}
        c["degree_snippets"] = degree_snippets(j["desc"])
        if j["us"] == "unknown":
            c["location_snippets"] = location_snippets(j["desc"])
        candidates.append(c)
    now_et = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5)))
    # US Eastern: UTC-4 from 2nd Sunday of March to 1st Sunday of November, else UTC-5.
    y = now_et.year
    dst_start = dt.datetime(y, 3, 8 + (6 - dt.date(y, 3, 8).weekday()) % 7, 2)
    dst_end = dt.datetime(y, 11, 1 + (6 - dt.date(y, 11, 1).weekday()) % 7, 2)
    naive = now_et.replace(tzinfo=None)
    if dst_start <= naive + dt.timedelta(hours=0) < dst_end:
        now_et = now_et + dt.timedelta(hours=1)
    print(json.dumps({"today_et": now_et.strftime("%Y-%m-%d"), "now_et": now_et.strftime("%Y-%m-%d %H:%M"),
                      "total_jobs_on_boards": len(jobs), "new_postings_any_type": len(new),
                      "candidates": candidates, "source_errors": errors}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
