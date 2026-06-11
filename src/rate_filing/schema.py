"""Single source of truth for the output workbook contract.

SCOPE: Bill Pay only. The pipeline reads a parsed insurance rate-filing document
and extracts ONLY bill-pay / payment-plan / billing-fee information into one
flat table. One row per payment plan (or per flat billing fee). The LLM does the
semantic extraction; each row carries an Accuracy score (the model's confidence)
and a Source Page for verification and the upcoming RAG phase (a `chunks` column
will be added then).

Works for any carrier's filing (GEICO, Progressive, and others) — nothing is
carrier-specific.

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

@dataclass(frozen=True)
class SheetSpec:
    name: str
    title: str
    note: str
    columns: list[str]


BILL_PAY = SheetSpec(
    "Bill_Pay",
    "Bill Pay — payment plans & billing fees",
    "One row per payment plan (downpayment + each-installment value/unit) or per "
    "flat billing fee (NSF/Late/Renewal). Source File = the PDF the row came "
    "from (all filings are appended into this one sheet). SERFF #/RFC # are the "
    "filing identifiers (blank if the document carries none, e.g. a scanned "
    "manual). Effective Date is when the rule takes effect; End Date is its "
    "sunset/expiration (usually blank — filings stay in force until superseded). "
    "Accuracy Score is the model's confidence in the row. Source Page anchors it "
    "to the document; Chunks lists the vector-store chunk id(s) the row came "
    "from (page-provenance link into the Postgres `chunks` table).",
    ["Source File", "Company", "SERFF #", "RFC #", "Fee Type", "Payment Plan",
     "Fee", "Eligibility Rule", "Downpayment Amount", "Downpayment Unit",
     "Each Installment Value", "Each Installment Unit", "Effective Date",
     "End Date", "Source Page", "Accuracy Score", "Chunks"],
)

SHEETS: list[SheetSpec] = [BILL_PAY]
SHEET_BY_NAME: dict[str, SheetSpec] = {s.name: s for s in SHEETS}
SHEET_NAMES: list[str] = [s.name for s in SHEETS]
