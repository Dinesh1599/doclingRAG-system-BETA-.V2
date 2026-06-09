"""Bill-pay page finder + schema contract (offline; no LLM/network)."""

from rate_filing import billpay
from rate_filing.schema import BILL_PAY


def _doc(pages_text: dict[int, str]) -> dict:
    return {"_normalized": True, "pages": sorted(pages_text),
            "texts": [{"page": p, "label": "text", "text": t}
                      for p, t in pages_text.items()],
            "tables": []}


def test_finds_billing_pages_and_skips_others():
    doc = _doc({
        1: "Rate manual cover page. State NJ.",
        2: "Rule B01 Bill Plans. Down payment and installment payments apply.",
        3: "Rule V12 Excess Vehicle Factor. Symbol relativities by model year.",
        4: "NSF fee and late fee charges; renewal payment billing.",
        5: "Territory definitions: ZIP to territory mapping.",
    })
    pages = billpay.find_billpay_pages(doc, log=lambda *a, **k: None)
    assert 2 in pages          # strong: bill plan / installment / down payment
    assert 4 in pages          # weak x>=2: NSF + late fee + billing + renewal
    assert 3 not in pages and 5 not in pages and 1 not in pages


def test_bill_pay_schema_columns():
    cols = BILL_PAY.columns
    assert cols[0] == "Company"
    for must in ("Fee Type", "Payment Plan", "Downpayment Amount",
                 "Each Installment Value", "Source Page", "Accuracy Score"):
        assert must in cols
