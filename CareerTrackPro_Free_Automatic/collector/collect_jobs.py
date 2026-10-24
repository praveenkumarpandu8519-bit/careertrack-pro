#!/usr/bin/env python3
"""Free public-ATS job collector for CareerTrack Pro."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "collector" / "companies.json"
OUT = ROOT / "data" / "jobs.json"

ROLE_PATTERNS = [
    ("Pharmacovigilance", re.compile(
        r"pharmacovigilance|drug safety|adverse event|safety case|"
        r"\bPV\b|safety surveillance", re.I)),
    ("Regulatory Affairs", re.compile(
        r"regulatory affairs|regulatory submission|regulatory operations|"
        r"regulatory specialist|regulatory compliance", re.I)),
    ("Medical Coding", re.compile(
        r"medical coder|medical coding|clinical coding|ICD[- ]?10", re.I)),
    ("Clinical Data Management", re.compile(
        r"clinical data management|clinical database|clinical data|"
        r"EDC\b|CDM\b|clinical data coordinator", re.I)),
    ("Clinical Research", re.compile(
        r"clinical research|clinical trial|clinical study|"
        r"research coordinator|trial assistant|clinical operations|"
        r"\bCRA\b|\bCRC\b|\bCTA\b", re.I)),
]

ENTRY = re.compile(
    r"\bfresher\b|\bentry[- ]level\b|\bgraduate\b|\btrainee\b|"
    r"\bjunior\b|\bintern(ship)?\b|\bnew grad(uate)?\b|"
    r"\b0\s*[-–to]+\s*1\s*years?\b",
    re.I,
)

SENIOR = re.compile(
    r"\bdirector\b|\bvice president\b|\bVP\b|\bhead of\b|"
    r"\bchief\b|\bprincipal\b|\bsenior director\b|"
    r"\bexecutive director\b|\bassociate director\b|"
    r"\b(?:8|9|10|11|12)\+?\s*years?\b",
    re.I,
)

OVERSEAS = re.compile(
    r"\b(remote|based)\s+(US|USA|United States|UK|United Kingdom|"
    r"China|Canada|Australia|Germany|France|Ireland|Singapore)\b|"
    r"\bUnited States\b|\bUnited Kingdom\b|\bCanada\b|"
    r"\bAustralia\b|\bGermany\b|\bFrance\b|\bSingapore\b",
    re.I,
)

INDIA_LOCATION = re.compile(
    r"\bIndia\b|\bHyderabad\b|\bBengaluru\b|\bBangalore\b|"
    r"\bChennai\b|\bMumbai\b|\bPune\b|\bGurugram\b|\bGurgaon\b|"
    r"\bNoida\b|\bDelhi\b|\bKolkata\b|\bAhmedabad\b|\bHindupur\b|"
    r"\bAnantapur\b|\bVisakhapatnam\b|\bVijayawada\b",
    re.I,
)

def fetch_json(url: str):
    request = Request(
        url,
        headers={
            "User-Agent": "CareerTrackPro/1.0 (public job-board collector)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8", errors="replace"))

def clean_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(x) for x in value)
    if isinstance(value, dict):
        return " ".join(clean_text(x) for x in value.values())
    return re.sub(r"<[^>]+>", " ", str(value)).replace("&nbsp;", " ").strip()

def area_for(title: str, description: str, department: str) -> str:
    # Prefer the title, then department, then description.
    # This avoids classifying a PV job from unrelated description text.
    for text in (title, department, description):
        for area, pattern in ROLE_PATTERNS:
            if pattern.search(text):
                return area
    return ""

def parse_posted(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return str(value)

def normalize(company: str, platform: str, raw: dict) -> dict | None:
    if platform == "greenhouse":
        title = raw.get("title", "")
        location = clean_text((raw.get("location") or {}).get("name", ""))
        url = raw.get("absolute_url", "")
        posted = raw.get("updated_at") or raw.get("created_at") or ""
        description = clean_text(raw.get("content", ""))
        job_id = str(raw.get("id", ""))
        department = clean_text(raw.get("departments", []))

    elif platform == "lever":
        title = raw.get("text", "")
        categories = raw.get("categories") or {}
        location = clean_text(categories.get("location", ""))
        url = raw.get("hostedUrl") or raw.get("applyUrl", "")
        posted = raw.get("createdAt", "")
        description = clean_text(
            raw.get("descriptionPlain") or raw.get("description", "")
        )
        job_id = str(raw.get("id", ""))
        department = clean_text([
            categories.get("team", ""),
            categories.get("department", ""),
        ])

    elif platform == "ashby":
        title = raw.get("title", "")
        location = clean_text(raw.get("location", ""))
        url = raw.get("jobUrl") or raw.get("applyUrl", "")
        posted = raw.get("publishedAt", "")
        description = clean_text(
            raw.get("descriptionPlain") or raw.get("descriptionHtml", "")
        )
        job_id = str(raw.get("id", "")) or url
        department = clean_text([
            raw.get("department", ""), raw.get("team", "")
        ])
        if raw.get("isListed") is False:
            return None
    else:
        return None

    if not title or not url or urlparse(url).scheme != "https":
        return None

    area = area_for(title, description, department)
    if not area:
        return None

    # Do not treat the word "remote" by itself as evidence of India eligibility.
    location_text = location.strip()
    if OVERSEAS.search(location_text):
        return None

    if not INDIA_LOCATION.search(location_text):
        # Allow an explicitly India-eligible remote role only when the
        # employer's listing says India in its location.
        return None

    # Do not infer fresher eligibility from "associate" or "assistant".
    eligibility_text = f"{title} {description[:5000]}"
    if SENIOR.search(title):
        level = "Experienced — check requirements"
    elif ENTRY.search(eligibility_text):
        level = "Potential entry-level — verify requirements"
    else:
        level = "Check eligibility"

    company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    posted_date = parse_posted(posted)

    return {
        "id": f"{platform}:{company_slug}:{job_id}",
        "title": title.strip(),
        "company": company,
        "location": location or "See employer posting",
        "area": area,
        "level": level,
        "type": "See employer posting",
        "date": posted_date[:10] if len(posted_date) >= 10 else "",
        "postedDate": posted_date,
        "discoveredAt": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "source": platform.title(),
        "sourceId": job_id,
        "live": True,
        "sample": False,
        "status": "To Apply",
    }

def collect_one(item: dict) -> tuple[list[dict], str]:
    company = str(item.get("company", "")).strip()
    platform = str(item.get("platform", "")).strip().lower()
    token = str(item.get("board_token") or item.get("site_name") or "").strip()

    if (
        not company
        or company.lower().startswith("add ")
        or not token
        or platform not in {"greenhouse", "lever", "ashby"}
    ):
        return [], "skipped (configure company + valid board token/site name)"

    try:
        if platform == "greenhouse":
            payload = fetch_json(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            )
            raw_jobs = payload.get("jobs", [])
        elif platform == "lever":
            payload = fetch_json(
                f"https://api.lever.co/v0/postings/{token}?mode=json"
            )
            raw_jobs = payload if isinstance(payload, list) else []
        else:
            payload = fetch_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{token}"
            )
            raw_jobs = payload.get("jobs", [])

        normalized = []
        for raw in raw_jobs:
            job = normalize(company, platform, raw)
            if job:
                normalized.append(job)

        return normalized, (
            f"ok: {len(raw_jobs)} postings scanned, {len(normalized)} relevant"
        )
    except (
        HTTPError, URLError, TimeoutError, ValueError, RuntimeError, OSError
    ) as exc:
        return [], f"error: {type(exc).__name__}: {exc}"

def sort_key(job: dict) -> tuple[float, str]:
    posted = job.get("postedDate") or job.get("date") or ""
    timestamp = 0.0
    try:
        if isinstance(posted, (int, float)):
            timestamp = float(posted) / 1000
        elif posted:
            timestamp = datetime.fromisoformat(
                str(posted).replace("Z", "+00:00")
            ).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        timestamp = 0.0
    return timestamp, str(job.get("discoveredAt") or "")

def main() -> int:
    if not CONFIG.exists():
        print(f"ERROR: Configuration file not found: {CONFIG}")
        return 1

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(cfg.get("companies"), list):
        print("ERROR: companies.json must contain a companies list.")
        return 1

    prior = {"jobs": []}
    if OUT.exists():
        try:
            prior = json.loads(OUT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"ERROR: Could not read existing jobs.json: {exc}")
            return 1

    existing = {
        job.get("id"): job
        for job in prior.get("jobs", [])
        if job.get("id")
    }
    existing_by_url = {
        job.get("url"): job
        for job in prior.get("jobs", [])
        if job.get("url")
    }

    summary = {}
    errors = []
    total_raw = 0

    for item in cfg["companies"]:
        found, state = collect_one(item)
        name = item.get("company", item.get("board_token", "unknown"))
        summary[name] = state
        if state.startswith("error:"):
            errors.append(f"{name}: {state}")

        for job in found:
            total_raw += 1
            old = existing.get(job["id"]) or existing_by_url.get(job["url"])
            if old:
                old_status = old.get("status", "To Apply")
                old_discovered = old.get("discoveredAt", job["discoveredAt"])
                old.update(job)
                old["status"] = old_status
                old["discoveredAt"] = old_discovered
                existing[job["id"]] = old
                existing_by_url[job["url"]] = old
            else:
                existing[job["id"]] = job
                existing_by_url[job["url"]] = job

    unique = {}
    for job in existing.values():
        key = job.get("url") or job.get("id")
        if (
            key not in unique
            or job.get("discoveredAt", "") < unique[key].get("discoveredAt", "")
        ):
            unique[key] = job

    jobs = sorted(unique.values(), key=sort_key, reverse=True)
    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSummary": summary,
        "scannedRelevantCount": total_raw,
        "errors": errors,
        "jobs": jobs,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Feed generated: {len(jobs)} retained listings; "
        f"{total_raw} relevant matches seen this run."
    )
    for name, state in summary.items():
        print(f"- {name}: {state}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
