"""Map real NAV ads into the Job record shape B2 consumes.

CRITICAL HONESTY RULE: this adapter fills structured fields ONLY from NAV's
own structured data. It never parses requirements out of the description to
populate `requirements.*`. That is deliberate — the entire question of the
external validation is how much of the real requirement surface is missing
from structured data, and a clever parser here would hide exactly the effect
we are trying to measure.

Consequences, stated plainly:
- `requirements.*` is essentially EMPTY for real ads (NAV publishes no
  machine-readable requirement fields), so the deterministic hard-constraint
  filter can only enforce what NAV structures: work location, position
  extent, engagement type, and the application deadline.
- Everything else — licence, certification, authorization, language, shifts,
  physical demands, travel — reaches B2 only through the raw ad text in the
  rerank prompt. Measuring what that costs is the point of Step 5.

The frozen V2 schemas are reused read-only; nothing here writes to them.
"""

from __future__ import annotations

import datetime as _dt
import re

from ...schemas import Job
from .analyze import plain_text

# NAV county -> a representative city known to the frozen commute table, used
# ONLY so the deterministic geography check has something to reason about.
# Unmapped locations fall back to the candidate's own city (i.e. geography is
# treated as non-binding rather than silently excluding the ad); every such
# fallback is recorded so the report can quantify it.
_COUNTY_CITY = {
    "OSLO": "Oslo",
    "AKERSHUS": "Lillestrøm",
    "BUSKERUD": "Drammen",
    "ØSTFOLD": "Fredrikstad",
    "INNLANDET": "Hamar",
    "TRØNDELAG": "Trondheim",
    "VESTLAND": "Bergen",
    "ROGALAND": "Stavanger",
    "TROMS": "Tromsø",
}
_CITY_DIRECT = {
    "OSLO": "Oslo", "BERGEN": "Bergen", "TRONDHEIM": "Trondheim",
    "STAVANGER": "Stavanger", "SANDNES": "Sandnes", "TROMSØ": "Tromsø",
    "DRAMMEN": "Drammen", "FREDRIKSTAD": "Fredrikstad", "SARPSBORG": "Sarpsborg",
    "MOSS": "Moss", "HAMAR": "Hamar", "ELVERUM": "Elverum",
    "LILLESTRØM": "Lillestrøm", "STJØRDAL": "Stjørdal",
}

_PCT = re.compile(r"(\d{2,3})\s*%")


def _location(ad: dict, fallback_city: str) -> tuple[str, bool]:
    """(city, was_fallback)."""
    for loc in (ad.get("work_locations") or []):
        city = ((loc or {}).get("city") or "").strip().upper()
        if city in _CITY_DIRECT:
            return _CITY_DIRECT[city], False
        county = ((loc or {}).get("county") or "").strip().upper()
        if county in _COUNTY_CITY:
            return _COUNTY_CITY[county], False
    return fallback_city, True


def _percent(ad: dict) -> int:
    extent = (ad.get("extent") or "").strip().lower()
    if extent == "heltid":
        return 100
    if extent == "deltid":
        # NAV does not publish the percentage; look for an explicit figure in
        # the text purely to avoid inventing 100%. If absent, 50 is a stated
        # assumption, recorded in the validation report.
        m = _PCT.search(plain_text(ad.get("description_html"))[:4000])
        if m:
            val = int(m.group(1))
            if 5 <= val <= 100:
                return val
        return 50
    return 100


def _deadline(ad: dict) -> str | None:
    """NAV `applicationDue` is free text ('18.09.2026', 'Snarest', ...)."""
    raw = (ad.get("application_due") or "").strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if m:
        d, mo, y = m.groups()
        try:
            return _dt.date(int(y), int(mo), int(d)).isoformat()
        except ValueError:
            return None
    if ad.get("expires"):
        try:
            return _dt.datetime.fromisoformat(ad["expires"]).date().isoformat()
        except (ValueError, TypeError):
            return None
    return None


def _sector(ad: dict) -> str:
    s = (ad.get("sector") or "").strip().lower()
    cats = " ".join((oc or {}).get("level1", "") for oc in
                    (ad.get("occupation_categories") or [])).lower()
    if "helse" in cats or "pleie" in cats:
        return "helse"
    if "it" in cats or "teknolog" in cats or "ingeniør" in cats:
        return "teknologi"
    if "bygg" in cats or "anlegg" in cats or "håndverk" in cats:
        return "bygg_anlegg"
    if "industri" in cats or "produksjon" in cats:
        return "industri"
    if "transport" in cats or "logistikk" in cats or "lager" in cats:
        return "logistikk"
    if "butikk" in cats or "salg" in cats or "service" in cats or "reiseliv" in cats:
        return "handel"
    if "barn" in cats or "undervis" in cats or "skole" in cats:
        return "utdanning"
    if "økonomi" in cats or "kontor" in cats or "administra" in cats or "jus" in cats:
        return "finans"
    if "natur" in cats or "fiske" in cats or "landbruk" in cats:
        return "havbruk"
    return "offentlig" if s == "offentlig" else "industri"


def nav_ad_to_job(ad: dict, job_id: str, fallback_city: str) -> tuple[Job, dict]:
    """Returns (Job, adaptation_notes). Requirements stay empty by design."""
    city, geo_fallback = _location(ad, fallback_city)
    text = plain_text(ad.get("description_html"))
    title = ad.get("title") or ad.get("jobtitle") or "(uten tittel)"
    employer = ad.get("employer_name") or "(ukjent arbeidsgiver)"

    # The ad text the model sees: NAV's own description, plus the structured
    # facts NAV actually publishes. Nothing invented.
    struct_lines = []
    if ad.get("extent"):
        struct_lines.append(f"Omfang: {ad['extent']}")
    if ad.get("engagementtype"):
        struct_lines.append(f"Ansettelsesform: {ad['engagementtype']}")
    if ad.get("positioncount"):
        struct_lines.append(f"Antall stillinger: {ad['positioncount']}")
    if ad.get("application_due"):
        struct_lines.append(f"Søknadsfrist: {ad['application_due']}")
    for oc in (ad.get("occupation_categories") or [])[:2]:
        struct_lines.append(f"Yrkeskategori: {oc.get('level1')} / {oc.get('level2')}")
    locs = [f"{(l or {}).get('city')} ({(l or {}).get('county')})"
            for l in (ad.get("work_locations") or [])]
    if locs:
        struct_lines.append("Arbeidssted: " + ", ".join(filter(None, locs)))

    description = "\n".join(struct_lines + ["", text]) if struct_lines else text

    job = Job.from_dict({
        "id": job_id,
        "title": title[:200],
        "employer": employer[:200],
        "sector": _sector(ad),
        "location_city": city,
        "work_mode": "onsite",          # NAV publishes no work-mode field
        "percent_position": _percent(ad),
        "shifts": [],                    # NAV publishes no shift field -> unstated
        "salary_nok_min": None,          # NAV publishes no salary field
        "salary_nok_max": None,
        "requires_overnight_travel": False,  # not structured by NAV
        "application_deadline": _deadline(ad),
        "structured_completeness": "minimal",
        "requirements": {
            "must_have_skills": [],
            "nice_to_have_skills": [],
            "min_years_experience": 0,
            "certifications_required": [],
            "driving_license_required": None,
            "norwegian_min_level": None,
            "english_min_level": None,
            "requires_norwegian_citizenship": False,
            "physical_demands": [],
        },
        "description": description[:6000],
    })
    notes = {
        "nav_uuid": ad.get("uuid"),
        "geography_fallback_used": geo_fallback,
        "nav_city": (ad.get("work_locations") or [{}])[0].get("city"),
        "nav_county": (ad.get("work_locations") or [{}])[0].get("county"),
        "extent_raw": ad.get("extent"),
        "percent_assumed": _percent(ad) == 50 and (ad.get("extent") or "").lower() == "deltid",
        "deadline_raw": ad.get("application_due"),
        "description_chars": len(text),
    }
    return job, notes
