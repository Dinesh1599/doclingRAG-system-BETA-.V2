"""Schema mapping (LLM, gpt-4o).

Given a detected table's header labels and a target sheet, map each source
label to a target column and emit a confidence. Low confidence routes the table
to quarantine for human review rather than forcing a fit. The LLM maps labels
only; deterministic code copies the cell values.
"""

from .clients import structured
from .config import Config
from .schema import SHEET_BY_NAME

CONFIDENCE_THRESHOLD = 0.6

_SYSTEM = (
    "You map a source table's column labels to a fixed target schema for an "
    "auto-insurance rate filing. Return, for each TARGET column, the source "
    "column index (0-based) that supplies it, or null if none. Also return an "
    "overall confidence in [0,1]. Map labels and positions only; never invent "
    "or transcribe data values."
)


def _schema_for(target_columns: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mapping": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "target_column": {"type": "string"},
                        "source_index": {"type": ["integer", "null"]},
                    },
                    "required": ["target_column", "source_index"],
                },
            },
            "confidence": {"type": "number"},
            "notes": {"type": "string"},
        },
        "required": ["mapping", "confidence", "notes"],
    }


def map_table(target_sheet: str, source_header: list[str],
              sample_rows: list[list[str]], cfg: Config) -> dict:
    spec = SHEET_BY_NAME[target_sheet]
    targets = [c for c in spec.columns if c not in ("Company", "NAIC", "State", "Line")]
    sample = "\n".join(" | ".join(r) for r in sample_rows[:5])
    user = (f"TARGET sheet: {target_sheet}\n"
            f"TARGET columns: {targets}\n\n"
            f"SOURCE header (by index): {list(enumerate(source_header))}\n"
            f"SOURCE sample rows:\n{sample}\n\n"
            "Return the source_index feeding each target column (or null).")
    res = structured(cfg.model_extract, _SYSTEM, user,
                     _schema_for(targets), "schema_mapping")
    col_to_idx = {m["target_column"]: m["source_index"]
                  for m in res.get("mapping", [])
                  if m["target_column"] in targets}
    return {"target_sheet": target_sheet, "col_to_idx": col_to_idx,
            "confidence": float(res.get("confidence", 0.0)),
            "notes": res.get("notes", "")}
