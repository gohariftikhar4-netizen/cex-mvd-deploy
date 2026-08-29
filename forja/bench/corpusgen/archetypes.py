"""Per-candidate generation targets (generation/eval side ONLY — workflows
never import this). Encodes which occupation families are true matches, which
single-dimension traps to plant, and goal-mismatch families ("obvious
occupation that is not the best next job").

Relation → grade rubric (V2, frozen with the corpus):
  strong (primary family, truth-eligible)        -> 2
  near   (near family, truth-eligible)           -> 1
  goal_mismatch family, even when eligible       -> 0 (stated goals override)
  any truth-ineligible job                       -> 0
  experience downgrade: if the job's required years > 2x the candidate's
  total experience (and > 2), a strong drops to near and a near drops to 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Archetype:
    candidate_id: str
    primary_families: tuple[str, ...]
    near_families: tuple[str, ...] = ()
    goal_mismatch_families: tuple[str, ...] = ()
    # single-dimension traps to plant (constraint dimension names, plus the
    # pseudo-dimension "experience")
    trap_dimensions: tuple[str, ...] = ()
    # dimension -> family to build the trap from (defaults to first primary)
    trap_family_overrides: dict = field(default_factory=dict)
    # certification id used for "certifications" traps
    trap_cert: str | None = None
    # (misleading title string, host family) — a job from `host family` whose
    # title sounds like the candidate's domain
    misleading: tuple[str, str] | None = None
    plant_strong: int = 6
    plant_near: int = 4


ARCHETYPES: dict[str, Archetype] = {a.candidate_id: a for a in [
    Archetype(
        "cand_ingrid",
        primary_families=("sykepleier", "ehelse_kundesuksess"),
        near_families=("legekontor",),
        trap_dimensions=("shifts", "percent_position", "deadline"),
        trap_cert=None,
        misleading=("Helsekonsulent salg", "salgsleder"),
    ),
    Archetype(
        "cand_marius",
        primary_families=("backend_dev", "devops_plattform"),
        near_families=("data_analyst",),
        trap_dimensions=("location_commute", "salary", "percent_position", "deadline"),
        misleading=("Utvikler barnehagemyndighet", "byggesak"),
    ),
    Archetype(
        "cand_amira",
        primary_families=("bygg_ingenior", "teknisk_tegner"),
        trap_dimensions=("language_norwegian", "work_authorization", "certifications", "deadline"),
        trap_cert="nokut_godkjent_ingeniorutdanning",
        misleading=("Konstruktør frisyre og form", "frisor"),
    ),
    Archetype(
        "cand_bjorn",
        primary_families=("butikksjef",),
        near_families=("kontaktsenter", "lager_leder", "salgsleder"),
        trap_dimensions=("shifts", "location_commute", "salary", "deadline"),
        misleading=("Butikkutvikler systemer", "backend_dev"),
    ),
    Archetype(
        "cand_silje",
        primary_families=("marinbiolog",),
        near_families=("akvatekniker", "labtekniker", "data_analyst"),
        trap_dimensions=("certifications", "percent_position", "deadline"),
        trap_cert="fiskehelsebiolog_autorisasjon",
        misleading=("Marin rådgiver kapitalforvaltning", "controller"),
    ),
    Archetype(
        "cand_tarik",
        primary_families=("elektro_kontor",),
        near_families=("automasjon",),
        trap_dimensions=("physical", "overnight_travel", "salary", "deadline"),
        trap_family_overrides={"physical": "elektriker", "overnight_travel": "elektriker"},
        misleading=("Elektrisk saksbehandler inkasso", "kontaktsenter"),
    ),
    Archetype(
        "cand_kari",
        primary_families=("edtech_innhold", "kommunikasjon"),
        near_families=("kursutvikler", "laerer"),
        trap_dimensions=("location_commute", "shifts", "percent_position", "deadline"),
        trap_family_overrides={"shifts": "kursutvikler"},
        misleading=("Innholdsansvarlig varelager", "lager_leder"),
    ),
    Archetype(
        "cand_pawel",
        primary_families=("lager",),
        near_families=("lager_leder",),
        trap_dimensions=("shifts", "driving_license", "language_norwegian", "deadline"),
        trap_family_overrides={"driving_license": "sjafor_varebil"},
        misleading=("Lagerføring regnskap", "regnskap"),
    ),
    Archetype(
        "cand_lene",
        primary_families=("controller", "regnskap"),
        near_families=("lonn_hr",),
        trap_dimensions=("percent_position", "certifications", "deadline"),
        trap_cert="autorisert_regnskapsforer",
        misleading=("Controller vareflyt natt", "lager_leder"),
    ),
    Archetype(
        "cand_oddvar",
        primary_families=("sjafor_ce", "renovasjon"),
        near_families=("lager",),
        trap_dimensions=("overnight_travel", "driving_license", "certifications",
                         "language_english", "shifts", "deadline"),
        trap_family_overrides={"driving_license": "bussjafor", "certifications": "lager"},
        trap_cert="truckforerbevis_t1_t4",
        misleading=("Transportplanlegger IT-systemer", "backend_dev"),
    ),
    # ---- V2 additions ----
    Archetype(
        "cand_yusuf",
        primary_families=("it_support",),
        near_families=("kontaktsenter", "resepsjon"),
        goal_mismatch_families=("taxi",),
        trap_dimensions=("shifts", "deadline"),
        trap_family_overrides={"shifts": "taxi"},
        misleading=("Sjåfør for digitale løsninger", "taxi"),
        plant_strong=5,
    ),
    Archetype(
        "cand_marta",
        primary_families=("helsefagarbeider",),
        trap_dimensions=("certifications", "language_norwegian", "deadline"),
        trap_family_overrides={"certifications": "sykepleier"},
        trap_cert="hpr_autorisasjon_sykepleier",
        misleading=("Sykepleiefaglig selger", "salgsleder"),
        plant_strong=5,
    ),
    Archetype(
        "cand_henrik",
        primary_families=("data_analyst",),
        near_families=("devops_plattform",),
        trap_dimensions=("salary", "experience", "deadline"),
        misleading=("Forsker kundedialog", "kontaktsenter"),
    ),
    Archetype(
        "cand_fatima",
        primary_families=("apotek",),
        near_families=("butikkmedarbeider",),
        trap_dimensions=("certifications", "language_norwegian", "deadline"),
        trap_cert="hpr_autorisasjon_farmasoyt",
        misleading=("Farmasøytisk regnskapsfører", "regnskap"),
        plant_strong=5,
    ),
    Archetype(
        "cand_geir",
        primary_families=("byggeleder",),
        near_families=("byggesak",),
        trap_dimensions=("physical", "overnight_travel", "deadline"),
        trap_family_overrides={"physical": "tomrer"},
        misleading=("Byggeleder digitale plattformer", "devops_plattform"),
    ),
    Archetype(
        "cand_solveig",
        primary_families=("helsefagarbeider",),
        near_families=("legekontor",),
        trap_dimensions=("shifts", "deadline"),
        misleading=("Omsorgsfull kundebehandler bank", "kontaktsenter"),
        plant_strong=4, plant_near=3,
    ),
    Archetype(
        "cand_dmitri",
        primary_families=("salgsleder",),
        near_families=("kontaktsenter",),
        trap_dimensions=("salary", "deadline"),
        misleading=("Salgssjef renholdstjenester natt", "renhold"),
    ),
    Archetype(
        "cand_ragnhild",
        primary_families=("resepsjon",),
        near_families=("kontaktsenter", "legekontor"),
        trap_dimensions=("shifts", "overnight_travel", "deadline"),
        misleading=("Resepsjonsansvarlig serverdrift", "devops_plattform"),
    ),
    Archetype(
        "cand_omar",
        primary_families=("sjafor_varebil", "lager"),
        near_families=(),
        trap_dimensions=("certifications", "shifts", "driving_license", "deadline"),
        trap_family_overrides={"certifications": "sjafor_ce", "driving_license": "renovasjon"},
        trap_cert="ysk_gods",
        misleading=("Varebilansvarlig regnskap", "okonomi_junior"),
        plant_strong=5,
    ),
    Archetype(
        "cand_ingvild",
        primary_families=("okonomi_junior",),
        near_families=("kontaktsenter", "butikkmedarbeider"),
        trap_dimensions=("experience", "deadline"),
        trap_family_overrides={"experience": "controller"},
        misleading=("Økonom for tunge kjøretøy", "sjafor_ce"),
    ),
    Archetype(
        "cand_thomas",
        primary_families=("backend_dev",),
        near_families=(),
        goal_mismatch_families=("salgsleder",),
        trap_dimensions=("location_commute", "salary", "deadline"),
        misleading=("Senior utvikler av mennesker (HR)", "hr_radgiver"),
    ),
    Archetype(
        "cand_aisha",
        primary_families=("barnehage",),
        near_families=(),
        trap_dimensions=("certifications", "shifts", "deadline"),
        trap_cert="barnehagelarerutdanning",
        misleading=("Pedagogisk selger leker", "butikkmedarbeider"),
        plant_strong=5,
    ),
    Archetype(
        "cand_kjell",
        primary_families=("prosessoperator",),
        near_families=("automasjon",),
        trap_dimensions=("overnight_travel", "salary", "deadline"),
        misleading=("Prosessansvarlig søknadsbehandling", "byggesak"),
    ),
    Archetype(
        "cand_lin",
        primary_families=("ux_designer",),
        near_families=("grafisk",),
        trap_dimensions=("language_norwegian", "deadline"),
        misleading=("Designer av økonomirapporter", "controller"),
        plant_strong=5,
    ),
]}
