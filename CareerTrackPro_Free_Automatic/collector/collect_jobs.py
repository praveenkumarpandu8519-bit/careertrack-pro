#!/usr/bin/env python3
"""Free public-ATS job collector for CareerTrack Pro.

Only fetches publicly listed job-board postings from configured employer boards.
It does not bypass logins, CAPTCHAs, robots controls, or access restrictions.
"""
from __future__ import annotations
import json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "collector" / "companies.json"
OUT = ROOT / "data" / "jobs.json"
ROLES = {
    "Clinical Data Management": re.compile(r"clinical data|clinical database|data management|EDC\b", re.I),
    "Clinical Research": re.compile(r"clinical research|clinical trial|clinical study|research coordinator|trial assistant|clinical operations|CRA\b|CRC\b|CTA\b", re.I),
    "Pharmacovigilance": re.compile(r"pharmacovigilance|drug safety|adverse event|safety case|PV associate", re.I),
    "Regulatory Affairs": re.compile(r"regulatory affairs|regulatory submission|regulatory operations|regulatory specialist", re.I),
    "Medical Coding": re.compile(r"medical coder|medical coding|clinical coding|ICD-10", re.I),
}
ENTRY = re.compile(r"fresher|entry.level|graduate|trainee|junior|associate|assistant|intern|0\s*[-–to]+\s*1\s*years?|new grad", re.I)
INDIA = re.compile(r"india|hyderabad|bengaluru|bangalore|chennai|mumbai|pune|gurugram|gurgaon|noida|delhi|kolkata|ahmedabad|remote", re.I)

def fetch_json(url: str):
    req = Request(url, headers={"User-Agent": "CareerTrackPro/1.0 (public job-board collector)", "Accept": "application/json"})
    with urlopen(req, timeout=25) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def area_for(text: str) -> str:
    for area, pattern in ROLES.items():
        if pattern.search(text):
            return area
    return ""

def clean_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(clean_text(v) for v in value.values())
    return re.sub(r"<[^>]+>", " ", str(value)).replace("&nbsp;", " ").strip()

def normalize(company: str, platform: str, raw: dict) -> dict | None:
    if platform == "greenhouse":
        title = raw.get("title", "")
        location = clean_text((raw.get("location") or {}).get("name", ""))
        url = raw.get("absolute_url", "")
        posted = raw.get("updated_at") or raw.get("created_at") or ""
        description = clean_text(raw.get("content", ""))
        jid = str(raw.get("id", ""))
        department = clean_text(raw.get("departments", []))
    elif platform == "lever":
        title = raw.get("text", "")
        categories = raw.get("categories") or {}
        location = clean_text(categories.get("location", ""))
        url = raw.get("hostedUrl") or raw.get("applyUrl", "")
        posted = raw.get("createdAt", "")
        description = clean_text(raw.get("descriptionPlain") or raw.get("description", ""))
        jid = str(raw.get("id", ""))
        department = clean_text([categories.get("team", ""), categories.get("department", "")])
    elif platform == "ashby":
        title = raw.get("title", "")
        location = clean_text(raw.get("location", ""))
        url = raw.get("jobUrl") or raw.get("applyUrl", "")
        posted = raw.get("publishedAt", "")
        description = clean_text(raw.get("descriptionPlain") or raw.get("descriptionHtml", ""))
        jid = str(raw.get("id", "")) or url
        department = clean_text([raw.get("department", ""), raw.get("team", "")])
        if raw.get("isListed") is False:
            return None
    else:
        return None

    if not title or not url or urlparse(url).scheme != "https":
        return None
    text = " ".join([title, description, department])
    area = area_for(text)
    if not area:
        return None
    # Keep jobs with explicit India/location signals or remote roles; user can tune this later.
    combined_location = location + " " + description[:1500]
    if not INDIA.search(combined_location):
        return None
    # Entry-level matching is advisory: keep a vacancy if its title or description hints
    # at early-career eligibility. Do not reject every job that omits experience details.
    early = bool(ENTRY.search(title + " " + description[:5000]))
    company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    return {
        "id": f"{platform}:{company_slug}:{jid}",
        "title": title.strip(),
        "company": company,
        "location": location or "See employer posting",
        "area": area,
        "level": "Fresher-friendly" if early else "Check eligibility",
        "type": "See employer posting",
        "date": (posted[:10] if isinstance(posted, str) and len(posted) >= 10 else ""),
        "postedDate": posted,
        "discoveredAt": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "source": platform.title(),
        "sourceId": jid,
        "live": True,
        "sample": False,
        "status": "To Apply",
    }

def collect_one(item: dict) -> tuple[list[dict], str]:
    company = item.get("company", "").strip()
    platform = item.get("platform", "").strip().lower()
    token = (item.get("board_token") or item.get("site_name") or "").strip()
    if not company or not token or platform not in {"greenhouse", "lever", "ashby"}:
        return [], "skipped (configure company + valid board token/site name)"
    try:
        if platform == "greenhouse":
            payload = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
            raw_jobs = payload.get("jobs", [])
        elif platform == "lever":
            payload = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
            raw_jobs = payload if isinstance(payload, list) else []
        else:
            payload = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
            raw_jobs = payload.get("jobs", [])
        normalized = [j for raw in raw_jobs if (j := normalize(company, platform, raw))]
        return normalized, f"ok: {len(raw_jobs)} postings scanned, {len(normalized)} relevant"
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
        return [], f"error: {type(exc).__name__}: {exc}"

def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    prior = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"jobs": []}
    existing = {j.get("id"): j for j in prior.get("jobs", []) if j.get("id")}
    existing_by_url = {j.get("url"): j for j in prior.get("jobs", []) if j.get("url")}
    summary, errors = {}, []
    total_raw = 0
    for item in cfg.get("companies", []):
        found, state = collect_one(item)
        name = item.get("company", item.get("board_token", "unknown"))
        summary[name] = state
        if state.startswith("error:"):
            errors.append(f"{name}: {state}")
        for job in found:
            total_raw += 1
            old = existing.get(job["id"]) or existing_by_url.get(job["url"])
            if old:
                # Refresh public fields but preserve the original discovery timestamp and status.
                status = old.get("status", "To Apply")
                discovered = old.get("discoveredAt", job["discoveredAt"])
                old.update(job)
                old["status"] = status
                old["discoveredAt"] = discovered
            else:
                existing[job["id"]] = job
                existing_by_url[job["url"]] = job
    # Merge URL duplicates and keep current listings. Older missing jobs are retained but marked
    # last-seen rather than deleted; the employer posting itself remains the authority.
    unique = {}
    for job in existing.values():
        key = job.get("url") or job.get("id")
        if key not in unique or job.get("discoveredAt", "") < unique[key].get("discoveredAt", ""):
            unique[key] = job
    jobs = sorted(unique.values(), key=lambda j: (j.get("postedDate") or j.get("date") or "", j.get("discoveredAt") or ""), reverse=True)
    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSummary": summary,
        "scannedRelevantCount": total_raw,
        "errors": errors,
        "jobs": jobs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Feed generated: {len(jobs)} retained listings; {total_raw} relevant matches seen this run.")
    for name, state in summary.items():
        print(f"- {name}: {state}")
    if not any(not state.startswith("skipped") for state in summary.values()):
        print("WARNING: No employer board is configured yet. Add valid board tokens in collector/companies.json.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
