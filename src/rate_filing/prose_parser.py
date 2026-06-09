"""Prose -> structured parameter rows (LLM, gpt-4o).

Converts prose rules (general rules, installment plans, discounts, surcharges,
eligibility) into typed parameter rows for sheets 21B / 22B / 02 / 03 / 08.
Numbers that appear in the prose are part of the rule text the LLM is reading,
not free-form computation; each row carries its source page for verification,
and numeric gates (e.g. installment fractions summing to 1.0) catch errors.
"""

from .clients import structured
from .config import Config

# ---- 21B General Rules -> parsed parameters -------------------------------

_GR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "Rule": {"type": "string"},
                    "Title": {"type": "string"},
                    "Category": {"type": "string"},
                    "Parameter": {"type": "string"},
                    "Value": {"type": "string"},
                    "Unit": {"type": "string"},
                    "Applies To / Condition": {"type": "string"},
                },
                "required": ["Rule", "Title", "Category", "Parameter", "Value",
                             "Unit", "Applies To / Condition"],
            },
        }
    },
    "required": ["rows"],
}

_GR_SYSTEM = (
    "You convert auto-insurance General Rules prose into one structured row per "
    "atomic parameter. Keep values exactly as written in the source (copy "
    "numbers verbatim, do not compute). One row per parameter/value."
)

# ---- 22B Installment plans -> per-payment schedule ------------------------

_INST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "Plan ID": {"type": "string"},
                    "Plan Name": {"type": "string"},
                    "Enrollment Required": {"type": "string"},
                    "Context": {"type": "string"},
                    "Payment #": {"type": "string"},
                    "Fraction Due": {"type": "string"},
                    "Fraction (decimal)": {"type": "number"},
                    "Timing / Due Date": {"type": "string"},
                },
                "required": ["Plan ID", "Plan Name", "Enrollment Required",
                             "Context", "Payment #", "Fraction Due",
                             "Fraction (decimal)", "Timing / Due Date"],
            },
        }
    },
    "required": ["rows"],
}

_INST_SYSTEM = (
    "You convert auto-insurance installment payment plan prose into one row per "
    "payment step. Fraction (decimal) is the portion of total premium due at "
    "that step; per (Plan ID, Context) the fractions MUST sum to 1.0. Copy the "
    "fractions exactly as the source expresses them (e.g. 1/6 -> 0.1667)."
)


def parse_general_rules(prose: str, source: str, cfg: Config) -> list[dict]:
    user = f"Source: {source}\n\nGeneral Rules text:\n{prose}"
    res = structured(cfg.model_extract, _GR_SYSTEM, user, _GR_SCHEMA,
                     "general_rules_parsed")
    rows = res.get("rows", [])
    for r in rows:
        r["Source"] = source
    return rows


def parse_installments(prose: str, source: str, cfg: Config) -> list[dict]:
    user = f"Source: {source}\n\nInstallment plans text:\n{prose}"
    res = structured(cfg.model_extract, _INST_SYSTEM, user, _INST_SCHEMA,
                     "installments_parsed")
    rows = res.get("rows", [])
    for r in rows:
        r["Source"] = source
    return rows
