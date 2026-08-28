"""Deterministic domain knowledge for the Norwegian employment domain.

Everything in this module is plain data + pure functions. It is the single
source of truth for controlled vocabularies used by schemas, the constraint
engine, matching, and the dataset. No LLM output may add to or reinterpret
anything defined here at runtime.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Language levels (CEFR). "native" sorts above C2.
# --------------------------------------------------------------------------

CEFR_ORDER = ["none", "A1", "A2", "B1", "B2", "C1", "C2", "native"]
_CEFR_RANK = {level: i for i, level in enumerate(CEFR_ORDER)}


def cefr_rank(level: str) -> int:
    if level not in _CEFR_RANK:
        raise ValueError(f"unknown CEFR level: {level!r}")
    return _CEFR_RANK[level]


def meets_language_level(candidate_level: str, required_level: str) -> bool:
    return cefr_rank(candidate_level) >= cefr_rank(required_level)


# --------------------------------------------------------------------------
# Driving licenses. Holding a class implies holding the classes it covers
# (simplified from the Norwegian førerkort rules; documented in BENCHMARK.md).
# --------------------------------------------------------------------------

DRIVING_LICENSE_CLASSES = ["B", "BE", "C1", "C", "CE", "D1", "D"]

_LICENSE_IMPLIES = {
    "B": set(),
    "BE": {"B"},
    "C1": {"B"},
    "C": {"B", "C1"},
    "CE": {"B", "C1", "C"},
    "D1": {"B"},
    "D": {"B", "D1"},
}


def expand_licenses(held: list[str]) -> set[str]:
    """All license classes effectively held, including implied ones."""
    out: set[str] = set()
    for cls in held:
        if cls not in _LICENSE_IMPLIES:
            raise ValueError(f"unknown driving license class: {cls!r}")
        out.add(cls)
        out |= _LICENSE_IMPLIES[cls]
    return out


# --------------------------------------------------------------------------
# Shift types and physical demands.
# A limitation named "no_<demand>" blocks the matching demand.
# --------------------------------------------------------------------------

SHIFT_TYPES = ["day", "evening", "night", "weekend"]

PHYSICAL_DEMANDS = ["heavy_lifting", "prolonged_standing", "working_at_heights"]

PHYSICAL_LIMITATIONS = ["no_" + d for d in PHYSICAL_DEMANDS]


def limitation_blocks_demand(limitation: str, demand: str) -> bool:
    return limitation == "no_" + demand


# --------------------------------------------------------------------------
# Work authorization.
# --------------------------------------------------------------------------

WORK_AUTH_STATUSES = [
    "citizen",           # Norwegian citizen
    "eea",               # EEA/EU national with registration
    "permanent_permit",  # permanent oppholdstillatelse with work rights
    "temporary_permit",  # temporary permit with work rights
]


# --------------------------------------------------------------------------
# Legally required certifications / authorizations (controlled vocabulary).
# Only credentials that are a legal or de-facto absolute requirement for the
# role belong in a job's `certifications_required`.
# --------------------------------------------------------------------------

CERTIFICATIONS = [
    "hpr_autorisasjon_sykepleier",       # HPR authorization, registered nurse
    "fagbrev_elektriker",                # trade certificate, electrician
    "fse_lavspenning",                   # FSE safety course, low voltage
    "truckforerbevis_t1_t4",             # forklift certificates T1–T4
    "adr_grunnkurs",                     # ADR dangerous-goods certificate
    "ysk_gods",                          # professional driver competence (goods)
    "nokut_godkjent_ingeniorutdanning",  # NOKUT-recognized engineering degree
    "autorisert_regnskapsforer",         # authorized accountant (Finanstilsynet)
    "ppu_pedagogisk_utdanning",          # teaching qualification (PPU/lektor)
    "fiskehelsebiolog_autorisasjon",     # authorized fish health biologist
]


# --------------------------------------------------------------------------
# Locations. City -> county (2024 county structure), plus a commute-minutes
# table for every city pair that occurs in the dataset. Unknown pairs are NOT
# guessed: commute_minutes raises, and the dataset integrity test asserts
# full coverage. Same-city commute is CITY_INTERNAL_MINUTES.
# --------------------------------------------------------------------------

CITY_COUNTY = {
    "Oslo": "Oslo",
    "Lillestrøm": "Akershus",
    "Drammen": "Buskerud",
    "Moss": "Østfold",
    "Fredrikstad": "Østfold",
    "Sarpsborg": "Østfold",
    "Hamar": "Innlandet",
    "Elverum": "Innlandet",
    "Trondheim": "Trøndelag",
    "Stjørdal": "Trøndelag",
    "Bergen": "Vestland",
    "Stavanger": "Rogaland",
    "Sandnes": "Rogaland",
    "Tromsø": "Troms",
}

CITY_INTERNAL_MINUTES = 20

# One-way door-to-door public-transport/car estimates, symmetric.
_COMMUTE_PAIRS: dict[frozenset[str], int] = {}


def _pair(a: str, b: str, minutes: int) -> None:
    _COMMUTE_PAIRS[frozenset((a, b))] = minutes


_pair("Oslo", "Lillestrøm", 20)
_pair("Oslo", "Drammen", 40)
_pair("Oslo", "Moss", 45)
_pair("Oslo", "Fredrikstad", 75)
_pair("Oslo", "Sarpsborg", 80)
_pair("Oslo", "Hamar", 75)
_pair("Oslo", "Elverum", 115)
_pair("Drammen", "Lillestrøm", 55)
_pair("Drammen", "Moss", 80)
_pair("Moss", "Fredrikstad", 30)
_pair("Moss", "Sarpsborg", 40)
_pair("Fredrikstad", "Sarpsborg", 20)
_pair("Hamar", "Elverum", 40)
_pair("Hamar", "Lillestrøm", 60)
_pair("Elverum", "Lillestrøm", 100)
_pair("Trondheim", "Stjørdal", 40)
_pair("Stavanger", "Sandnes", 25)

# Any pair of known cities WITHOUT an explicit entry is beyond commuting
# range by definition (the table above must list every genuinely commutable
# pair in the dataset; Norwegian cities outside it are hours apart).
NOT_COMMUTABLE = 10_000


def commute_minutes(city_a: str, city_b: str) -> int:
    for city in (city_a, city_b):
        if city not in CITY_COUNTY:
            raise ValueError(f"unknown city: {city!r}")
    if city_a == city_b:
        return CITY_INTERNAL_MINUTES
    return _COMMUTE_PAIRS.get(frozenset((city_a, city_b)), NOT_COMMUTABLE)


# --------------------------------------------------------------------------
# Canonical skills (controlled vocabulary) and free-text aliases.
# --------------------------------------------------------------------------

SKILLS = [
    # Healthcare
    "klinisk_sykepleie", "medikamenthandtering", "pasientveiledning", "triage",
    "journalforing_dips", "palliativ_pleie", "vaksinasjon", "saksbehandling_helse",
    # Software / tech
    "python", "go", "java", "typescript", "react", "kubernetes", "docker",
    "aws", "azure", "postgresql", "api_design", "microservices", "ci_cd",
    "terraform", "linux", "testing_automatisering", "informasjonssikkerhet",
    # Data / science
    "r_statistikk", "gis", "feltarbeid", "laboratoriearbeid", "fiskehelse",
    "akvakultur_drift", "miljoovervaking", "dataanalyse", "excel", "powerbi", "sql",
    # Engineering / construction
    "konstruksjonsberegning", "autocad", "etabs", "revit", "byggesak",
    "prosjektering", "teknisk_tegning", "betongkonstruksjoner",
    "stalkonstruksjoner", "hms", "kvalitetssikring_ks", "internkontroll",
    # Retail / management
    "personalledelse", "budsjettansvar", "varelogistikk", "kundeservice",
    "salg", "butikkdrift", "innkjop", "opplaering_ansatte", "vaktplanlegging",
    # Electrical
    "elektro_installasjon", "feilsoking_elektro", "ekom", "automasjon", "plc",
    # Education / content
    "undervisning", "laereplanarbeid", "klasseledelse", "innholdsproduksjon",
    "formidling", "kursutvikling", "digital_laering",
    # Logistics / transport
    "lagerarbeid", "truckkjoring", "plukk_pakk", "wms_system", "varemottak",
    "distribusjonskjoring", "ruteplanlegging", "lastsikring", "sjafor_tungbil",
    # Finance
    "regnskap", "ifrs", "konsolidering", "visma", "sap", "okonomistyring",
    "rapportering", "lonn", "arsoppgjor", "controlling", "mva",
    # General
    "prosjektledelse", "kommunikasjon", "kundeoppfolging", "kundesuksess",
    "teamledelse", "dokumentasjon", "saksbehandling", "crm_system",
    # Occupations outside the candidate pool (distractor jobs need real
    # requirements too, so irrelevance is a fact of the data, not an accident)
    "matlaging", "renhold", "pedagogisk_arbeid_barn", "frisering",
    "grafisk_design", "juridisk_radgivning", "sveising",
    "maskinkjoring_anlegg", "resepsjonsarbeid", "sosialt_arbeid",
]

_SKILL_SET = set(SKILLS)

# Free-text token/phrase -> canonical skill. Lowercased matching; used only by
# the deterministic profiling step. Deliberately conservative: an alias must be
# unambiguous.
SKILL_ALIASES = {
    "dips": "journalforing_dips",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "power bi": "powerbi",
    "golang": "go",
    "medikamenthåndtering": "medikamenthandtering",
    "sykepleie": "klinisk_sykepleie",
    "triagering": "triage",
    "førstelinje": "kundeservice",
    "kundeoppfølging": "kundeoppfolging",
    "undervisningserfaring": "undervisning",
    "læreplan": "laereplanarbeid",
    "truck": "truckkjoring",
    "lager": "lagerarbeid",
    "regnskapsføring": "regnskap",
    "årsoppgjør": "arsoppgjor",
    "økonomistyring": "okonomistyring",
    "el-installasjon": "elektro_installasjon",
    "feilsøking": "feilsoking_elektro",
    "kvalitetssikring": "kvalitetssikring_ks",
    "konstruksjon": "konstruksjonsberegning",
    "prosjekteringsledelse": "prosjektering",
}


def is_known_skill(skill: str) -> bool:
    return skill in _SKILL_SET


def normalize_skill(token: str) -> str | None:
    """Map a raw token/phrase to a canonical skill id, or None."""
    t = token.strip().lower()
    if t in _SKILL_SET:
        return t
    return SKILL_ALIASES.get(t)


# --------------------------------------------------------------------------
# Transferable skills: source skill -> targets it partially satisfies.
# Weight in (0, 1]: fraction of a direct skill match the transfer is worth in
# structured matching. Each entry carries a human-readable rationale that is
# quoted verbatim in recommendation evidence.
# --------------------------------------------------------------------------

TRANSFERABLE: dict[str, list[tuple[str, float, str]]] = {
    "pasientveiledning": [
        ("kundesuksess", 0.6, "veiledning av pasienter og pårørende er direkte overførbart til å følge opp og lære opp kunder"),
        ("kundeoppfolging", 0.7, "strukturert oppfølging av pasienter tilsvarer strukturert kundeoppfølging"),
        ("formidling", 0.6, "daglig formidling av helseinformasjon til ulike mottakere"),
    ],
    "klinisk_sykepleie": [
        ("saksbehandling_helse", 0.6, "klinisk vurdering og dokumentasjonskrav ligger tett på helsefaglig saksbehandling"),
    ],
    "triage": [
        ("kundeservice", 0.5, "prioritering av henvendelser under press er kjernen i god førstelinje"),
    ],
    "undervisning": [
        ("formidling", 0.9, "undervisning er profesjonell formidling"),
        ("kursutvikling", 0.8, "planlegging av undervisningsopplegg tilsvarer utvikling av kurs"),
        ("innholdsproduksjon", 0.6, "utarbeidelse av undervisningsmateriell er innholdsproduksjon"),
        ("kundesuksess", 0.5, "oppfølging av elever og foresatte ligner strukturert brukeroppfølging"),
    ],
    "klasseledelse": [
        ("teamledelse", 0.5, "å lede grupper mot definerte mål er overførbart til teamledelse"),
    ],
    "laereplanarbeid": [
        ("digital_laering", 0.6, "læreplanforståelse er kjernekompetanse i digitale læremidler"),
    ],
    "butikkdrift": [
        ("varemottak", 0.7, "daglig drift av butikk omfatter varemottak og varehåndtering"),
        ("kundesuksess", 0.4, "drift med kundetilfredshet som mål"),
    ],
    "varelogistikk": [
        ("lagerarbeid", 0.7, "vareflyt i butikk bygger på samme prinsipper som lagerdrift"),
        ("innkjop", 0.6, "bestilling og lagerstyring grenser mot innkjøpsfunksjonen"),
    ],
    "personalledelse": [
        ("teamledelse", 0.9, "personalansvar omfatter daglig teamledelse"),
        ("vaktplanlegging", 0.8, "bemanningsplanlegging følger av personalansvar"),
    ],
    "konstruksjonsberegning": [
        ("prosjektering", 0.8, "beregning av konstruksjoner er en kjernedel av prosjektering"),
        ("teknisk_tegning", 0.7, "konstruktører produserer og kontrollerer tekniske tegninger"),
    ],
    "autocad": [
        ("teknisk_tegning", 0.9, "AutoCAD-erfaring er dokumentert teknisk tegning"),
        ("revit", 0.5, "overgang mellom DAK-verktøy er kort for erfarne brukere"),
    ],
    "feilsoking_elektro": [
        ("automasjon", 0.6, "systematisk feilsøking i elektriske anlegg er kjernekompetanse i automasjon"),
    ],
    "elektro_installasjon": [
        ("internkontroll", 0.5, "installasjonsarbeid etter forskrift gir praktisk internkontrollkompetanse"),
    ],
    "laboratoriearbeid": [
        ("kvalitetssikring_ks", 0.5, "laboratorierutiner bygger på dokumentert kvalitetssikring"),
    ],
    "fiskehelse": [
        ("akvakultur_drift", 0.7, "fiskehelsearbeid forutsetter forståelse av driftssyklusen i anlegg"),
        ("miljoovervaking", 0.6, "helseovervåking i anlegg overlapper med miljøovervåking"),
    ],
    "distribusjonskjoring": [
        ("lagerarbeid", 0.5, "distribusjonssjåfører håndterer daglig varemottak og lasting"),
        ("ruteplanlegging", 0.8, "erfaring med effektiv rutegjennomføring"),
    ],
    "sjafor_tungbil": [
        ("lastsikring", 0.9, "tungbilerfaring omfatter dokumentert lastsikring"),
    ],
    "controlling": [
        ("rapportering", 0.9, "controlleroppgaver er i hovedsak strukturert rapportering"),
        ("okonomistyring", 0.8, "controlling er operativ økonomistyring"),
    ],
    "regnskap": [
        ("okonomistyring", 0.7, "regnskapsforståelse er grunnlaget for økonomistyring"),
        ("lonn", 0.5, "regnskapserfaring dekker normalt grensesnittet mot lønn"),
    ],
    "dokumentasjon": [
        ("saksbehandling", 0.5, "strukturert dokumentasjon er halve saksbehandlingen"),
    ],
}


def transfer_paths(candidate_skills: set[str], target_skill: str) -> list[tuple[str, float, str]]:
    """Transferable routes from any held skill to `target_skill`.

    Returns (source_skill, weight, rationale), best weight first.
    """
    paths = []
    for source in candidate_skills:
        for target, weight, rationale in TRANSFERABLE.get(source, []):
            if target == target_skill:
                paths.append((source, weight, rationale))
    paths.sort(key=lambda p: (-p[1], p[0]))
    return paths


# --------------------------------------------------------------------------
# Sectors (used for soft preference matching only — never a hard constraint).
# --------------------------------------------------------------------------

SECTORS = [
    "helse", "teknologi", "offentlig", "bygg_anlegg", "industri", "handel",
    "logistikk", "transport", "utdanning", "finans", "havbruk", "energi",
    "forsvar", "kultur",
]
