"""Match Engine v1 output contract.

Difference from the frozen V2 RANK_SCHEMA: every ranked item must carry an
explicit, machine-readable hard-constraint verdict. Constraint reasoning in
prose is not actionable; a boolean the code can enforce is.

Measured motivation (real NAV data, 77 strong-evidence conflicts): 87% of
conflicts were never elicited because no field asked for them, and 4% were
stated in the model's own prose and then ignored because nothing consumed it.
"""

from __future__ import annotations

# Dimensions the model must judge from the RAW ad text. NAV publishes no
# machine-readable requirement fields for any of these (measured: 0.0%
# structured coverage for 10 of 13 dimensions), so the text is the only source.
CONSTRAINT_DIMENSIONS = [
    "shifts",             # night / rotation / evening / weekend duty
    "authorization",      # norsk autorisasjon, HPR, licence to practise
    "certification",      # fagbrev, svennebrev, truckførerbevis, ADR ...
    "drivers_license",    # førerkort class
    "language",           # required Norwegian/English level
    "education",          # required completed education
    "experience",         # required years//type of experience
    "travel",             # overnight travel / rotation away from home
    "physical",           # lifting, standing, heights
    "work_authorization",  # citizenship / clearance / right to work
    "position_extent",    # percentage of full-time
    "deadline",           # application deadline already passed
]

_CONFLICT_ITEM = {
    "type": "object",
    "properties": {
        "dimension": {"type": "string", "enum": CONSTRAINT_DIMENSIONS},
        "quote": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["dimension", "quote", "explanation"],
    "additionalProperties": False,
}

_CLAIM = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "source": {"type": "string", "enum": ["candidate", "job"]},
        "quote": {"type": "string"},
    },
    "required": ["claim", "source", "quote"],
    "additionalProperties": False,
}

RANK_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "score": {"type": "number"},
                    # THE enforcement field. True => the engine rejects the job.
                    "hard_constraint_conflict": {"type": "boolean"},
                    "conflicts": {"type": "array", "items": _CONFLICT_ITEM},
                    "claims": {"type": "array", "items": _CLAIM},
                },
                "required": ["job_id", "score", "hard_constraint_conflict",
                             "conflicts", "claims"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

CONSTRAINT_INSTRUCTION = (
    "ABSOLUTTE KRAV — dette er viktigere enn rangeringen:\n"
    "Stillingsannonsene er IKKE forhåndsfiltrert på kravene under. NAV "
    "publiserer ingen maskinlesbare kravfelt, så kravene står som regel BARE i "
    "annonseteksten. For HVER stilling skal du derfor lese annonseteksten og "
    "vurdere om noen av disse dimensjonene bryter kandidatens absolutte krav:\n"
    "  " + ", ".join(CONSTRAINT_DIMENSIONS) + "\n"
    "Sett hard_constraint_conflict = true hvis minst én dimensjon bryter et "
    "absolutt krav, og oppgi hver konflikt i 'conflicts' med dimensjon, et "
    "ORDRETT sitat fra annonsen som viser kravet, og en kort forklaring.\n"
    "Sett hard_constraint_conflict = false bare når du har lest teksten og "
    "ingen dimensjon bryter et absolutt krav.\n"
    "Ikke ranger en stilling høyt fordi den ellers passer: en stilling med "
    "konflikt blir uansett avvist av systemet. Vær ærlig — det er bedre å "
    "melde en konflikt enn å skjule den."
)
