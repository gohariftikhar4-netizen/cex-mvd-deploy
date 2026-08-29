"""Read-only ingestion adapter for the official NAV Job Vacancy Feed.

    python3 -m forja.external.nav.ingest --target 2000 --out nav_data/snapshot_<date>

Design rules (see NAV_SOURCE.md for the verified source facts):

- READ-ONLY. Never writes to NAV; never touches the frozen V2 corpus, gold,
  or scoring. This module is deliberately outside forja/bench/.
- RAW PRESERVED. Every ingested ad stores NAV's complete `ad_content` record
  verbatim under `raw`, plus fetch provenance, so any downstream claim can be
  traced back to the exact NAV payload it came from.
- PERSONAL DATA. `contactList` is dropped by default (NAV terms require
  deleting personal data once unnecessary); `--keep-contacts` opts in.
- POLITE. Sequential, with a configurable delay; HTTP/1.1 pinned (see
  NAV_SOURCE.md §3).
- REPRODUCIBLE-ish. A snapshot records its exact anchor, token fingerprint
  (not the token), timestamps, and per-page cursors in `manifest.json`. Note
  the feed is an event log over live data: re-running later yields different
  ads. The snapshot file IS the reproducible artifact.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = f"{BASE}/api/publicToken"
FEED_URL = f"{BASE}/api/v1/feed"
USER_AGENT = "forja-external-validation/1.0 (research; contact via repo owner)"

_JWT_RE = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class NavIngestError(RuntimeError):
    pass


def _request(url: str, token: str, extra_headers: dict | None = None,
             timeout: float = 180.0, retries: int = 3) -> tuple[dict, dict]:
    """GET a JSON resource. Returns (payload, response_headers)."""
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/json",
               "User-Agent": USER_AGENT,
               **(extra_headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp), dict(resp.headers)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1) ** 2)
                last = e
                continue
            raise NavIngestError(f"HTTP {e.code} for {url}: {body}") from e
        except (TimeoutError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1) ** 2)
                last = e
                continue
            raise NavIngestError(f"connection failure for {url}: {e}") from e
    raise NavIngestError(f"retries exhausted for {url}: {last}")


def fetch_public_token() -> str:
    """The public-token endpoint returns prose containing the JWT."""
    req = urllib.request.Request(TOKEN_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    m = _JWT_RE.search(text)
    if not m:
        raise NavIngestError("no JWT found in publicToken response")
    return m.group(0)


def token_fingerprint(token: str) -> str:
    """Identify a token in the manifest WITHOUT recording the secret."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _clean_ad(wrapper: dict, keep_contacts: bool) -> dict | None:
    """Normalize one feed-entry wrapper into a snapshot record.

    Returns None for entries with no retrievable content (inactive/masked)."""
    content = wrapper.get("ad_content")
    if not content:
        return None
    raw = dict(content)
    if not keep_contacts:
        raw.pop("contactList", None)
    return {
        "uuid": wrapper.get("uuid") or raw.get("uuid"),
        "status": wrapper.get("status"),
        "sist_endret": wrapper.get("sistEndret"),
        # Convenience projection — `raw` remains authoritative.
        "title": raw.get("title"),
        "jobtitle": raw.get("jobtitle"),
        "employer_name": (raw.get("employer") or {}).get("name"),
        "employer_orgnr": (raw.get("employer") or {}).get("orgnr"),
        "description_html": raw.get("description"),
        "engagementtype": raw.get("engagementtype"),
        "extent": raw.get("extent"),
        "sector": raw.get("sector"),
        "positioncount": raw.get("positioncount"),
        "starttime": raw.get("starttime"),
        "application_due": raw.get("applicationDue"),
        "published": raw.get("published"),
        "expires": raw.get("expires"),
        "updated": raw.get("updated"),
        "work_locations": raw.get("workLocations"),
        "occupation_categories": raw.get("occupationCategories"),
        "category_list": raw.get("categoryList"),
        "source": raw.get("source"),
        "sourceurl": raw.get("sourceurl"),
        "application_url": raw.get("applicationUrl"),
        "link": raw.get("link"),
        # Full provenance: NAV's own payload, verbatim.
        "raw": raw,
        "_provenance": {
            "source": "NAV Job Vacancy Feed (pam-stilling-feed)",
            "detail_endpoint": f"{BASE}/api/v1/feedentry/{wrapper.get('uuid')}",
            "fetched_at": _dt.datetime.now(_dt.UTC).isoformat(),
        },
    }


def ingest(target: int, out_dir: Path, hours_back: int = 72,
           keep_contacts: bool = False, delay: float = 0.15,
           max_pages: int = 200) -> dict:
    token = fetch_public_token()
    anchor_dt = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=hours_back)
    anchor = anchor_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
    out_dir.mkdir(parents=True, exist_ok=True)

    started = _dt.datetime.now(_dt.UTC)
    ads: list[dict] = []
    seen_uuids: set[str] = set()
    pages = 0
    listed = active_listed = detail_fetch = detail_masked = 0
    cursors: list[str] = []

    url = FEED_URL
    headers = {"If-Modified-Since": anchor}
    while len(ads) < target and pages < max_pages:
        payload, _resp_headers = _request(url, token, headers)
        headers = {}  # anchor only applies to the first request
        pages += 1
        items = payload.get("items") or []
        listed += len(items)
        for item in items:
            entry = item.get("_feed_entry") or {}
            if entry.get("status") != "ACTIVE":
                continue
            active_listed += 1
            uuid = entry.get("uuid")
            if not uuid or uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)
            detail_url = f"{BASE}{item.get('url')}" if item.get("url", "").startswith("/") \
                else item.get("url")
            try:
                wrapper, _ = _request(detail_url, token)
            except NavIngestError as e:
                print(f"  ! detail fetch failed for {uuid}: {e}", flush=True)
                continue
            detail_fetch += 1
            record = _clean_ad(wrapper, keep_contacts)
            if record is None:
                detail_masked += 1  # went inactive between listing and fetch
            else:
                ads.append(record)
            time.sleep(delay)
            if len(ads) >= target:
                break
        next_url = payload.get("next_url")
        next_id = payload.get("next_id")
        print(f"  page {pages}: {len(items)} listed, {len(ads)}/{target} ads captured",
              flush=True)
        if not next_url or not next_id:
            break
        cursors.append(next_id)
        url = f"{BASE}{next_url}" if next_url.startswith("/") else next_url

    finished = _dt.datetime.now(_dt.UTC)
    corpus_path = out_dir / "nav_ads.json.gz"
    with gzip.open(corpus_path, "wt", encoding="utf-8") as f:
        json.dump(ads, f, ensure_ascii=False)

    manifest = {
        "source": "NAV Job Vacancy Feed (pam-stilling-feed)",
        "source_docs": "https://navikt.github.io/pam-stilling-feed/",
        "terms": "https://arbeidsplassen.nav.no/vilkar-api",
        "base_url": BASE,
        "auth": "public token (Bearer JWT)",
        "token_fingerprint": token_fingerprint(token),
        "snapshot_started_utc": started.isoformat(),
        "snapshot_finished_utc": finished.isoformat(),
        "if_modified_since_anchor": anchor,
        "hours_back": hours_back,
        "pages_read": pages,
        "page_cursors": cursors,
        "entries_listed": listed,
        "entries_listed_active": active_listed,
        "detail_fetches": detail_fetch,
        "detail_masked_inactive": detail_masked,
        "ads_captured": len(ads),
        "contacts_kept": keep_contacts,
        "corpus_file": corpus_path.name,
        "notes": [
            "Feed is an event log; the same ad reappears on update.",
            "Inactive ads are content-masked by NAV and cannot be recovered later.",
            "contactList dropped unless --keep-contacts (personal data, NAV terms).",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "page_cursors"},
                     indent=2, ensure_ascii=False))
    return manifest


def load_snapshot(snapshot_dir: Path) -> list[dict]:
    path = Path(snapshot_dir) / "nav_ads.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", type=int, default=1000, help="ads to capture")
    p.add_argument("--out", required=True)
    p.add_argument("--hours-back", type=int, default=72,
                   help="If-Modified-Since anchor, hours before now")
    p.add_argument("--keep-contacts", action="store_true",
                   help="retain contactList (personal data; off by default)")
    p.add_argument("--delay", type=float, default=0.15)
    p.add_argument("--max-pages", type=int, default=200)
    args = p.parse_args(argv)
    ingest(args.target, Path(args.out), hours_back=args.hours_back,
           keep_contacts=args.keep_contacts, delay=args.delay,
           max_pages=args.max_pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
