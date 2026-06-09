"""Single source of truth for the output workbook contract.

SCOPE: Bill Pay only. The pipeline reads a parsed insurance rate-filing document
and extracts ONLY bill-pay / payment-plan / billing-fee information into one
flat table. One row per payment plan (or per flat billing fee). The LLM does the
semantic extraction; each row carries an Accuracy score (the model's confidence)
and a Source Page for verification and the upcoming RAG phase (a `chunks` column
will be added then).

Works for any carrier's filing (GEICO, Progressive, and others) — nothing is
carrier-specific. See PLAN.md.

Workbook layout convention:
    row 1  -> TITLE banner
    row 2  -> NOTE
    row 3  -> blank spacer
    row 4  -> column HEADERS
    row 5+ -> data
"""

from dataclasses import dataclass

TITLE_ROW = 1
NOTE_ROW = 2
SPACER_ROW = 3
HEADER_ROW = 4
DATA_START_ROW = 5

# Kept for excel_writer compatibility; bill-pay rows carry Company explicitly.
METADATA_COLUMNS: list[str] = []

# Allowed values for the Fee Type column (open-ended; "Other" catches the rest).
FEE_TYPES = ["Installment", "NSF", "Late", "Renewal", "Other"]


@dataclass(frozen=True)
class SheetSpec:
    name: str
    title: str
    note: str
    columns: list[str]
    has_metadata: bool = False

    @property
    def page_col(self) -> str | None:
        for c in ("Source Page", "SourcePage", "Source"):
            if c in self.columns:
                return c
        return None


BILL_PAY = SheetSpec(
    "Bill_Pay",
    "Bill Pay — payment plans & billing fees",
    "One row per payment plan (downpayment + each-installment value/unit) or per "
    "flat billing fee (NSF/Late/Renewal). Accuracy Score is the model's "
    "confidence in the row. Source Page anchors it to the document.",
    ["Company", "Fee Type", "Payment Plan", "Fee", "Eligibility Rule",
     "Downpayment Amount", "Downpayment Unit", "Each Installment Value",
     "Each Installment Unit", "Source Page", "Accuracy Score"],
)

SHEETS: list[SheetSpec] = [BILL_PAY]
SHEET_BY_NAME: dict[str, SheetSpec] = {s.name: s for s in SHEETS}
SHEET_NAMES: list[str] = [s.name for s in SHEETS]
