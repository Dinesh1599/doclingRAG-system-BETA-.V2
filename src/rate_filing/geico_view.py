"""GEICO-view reshaper — cross-check only, NOT the product output.

The product output is the carrier-neutral workbook (see schema.py). This module
reshapes the relevant universal sheets back into the handful of GEICO-shaped
sheets that the old golden workbook used, so we can keep comparing GEICO's
extracted numbers against that golden.

IMPORTANT: the golden .xlsx was produced by another Claude run, not a verified
answer key (see project memory). So a disagreement does NOT mean we are wrong —
it means: go look at the actual PDF page and report which side matches the
document. This reshaper exists to surface those disagreements, nothing more.
"""

# Marital/gender order GEICO used as the 4 wide class-factor columns.
_CLASS_FACTOR_COLS = ["Single Male", "Single Female", "Married Male", "Married Female"]


def mbi_view(vehicle_rows: list[dict]) -> list[dict]:
    """06_Vehicle_Symbols (Symbol Type == 'MBI') -> old 19_MBI_Codes rows."""
    out = []
    for r in vehicle_rows:
        if r.get("Symbol Type") != "MBI":
            continue
        out.append({
            "Model Year": r.get("Model Year"), "Make": r.get("Make"),
            "Model": r.get("Model"), "Body Style": r.get("Body Style"),
            "Engine Type": r.get("Engine Type"),
            "Four Wheel Drive": r.get("Four Wheel Drive"),
            "MBI Code": r.get("Symbol"), "SourcePage": r.get("Source Page"),
        })
    return out


def vlr_view(vehicle_rows: list[dict]) -> list[dict]:
    """06_Vehicle_Symbols (Symbol Type == 'Liability') -> old 20_VLR_Symbols rows."""
    out = []
    for r in vehicle_rows:
        if r.get("Symbol Type") != "Liability":
            continue
        out.append({
            "Model Year": r.get("Model Year"), "Make": r.get("Make"),
            "Model": r.get("Model"), "Body Style": r.get("Body Style"),
            "Engine Type": r.get("Engine Type"),
            "Four Wheel Drive": r.get("Four Wheel Drive"),
            "Liability Symbol": r.get("Symbol"), "SourcePage": r.get("Source Page"),
        })
    return out


def territory_view(territory_rows: list[dict]) -> list[dict]:
    """05_Territory_Definitions -> old 18_Territory_Definitions rows."""
    return [{"ZIP Code": r.get("ZIP Code"),
             "Rating Territory": r.get("Rating Territory")}
            for r in territory_rows]


def class_factors_view(factor_rows: list[dict]) -> list[dict]:
    """04_Rating_Factors (Factor Type == 'Class Factor') -> wide 14_Class_Factors.

    Long form stores one row per (Coverage, Age, Marital/Gender). Pivot back to
    the wide GEICO layout keyed by (Coverage, Age of Driver)."""
    wide: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in factor_rows:
        if r.get("Factor Type") != "Class Factor":
            continue
        cov = r.get("Coverage")
        age = r.get("Dim1 Value")  # Dim1 = Age of Driver
        mg = r.get("Dim2 Value")   # Dim2 = Marital/Gender label
        key = (cov, age)
        if key not in wide:
            wide[key] = {"Coverage": cov, "Age of Driver": age,
                         **{c: None for c in _CLASS_FACTOR_COLS}}
            order.append(key)
        if mg in _CLASS_FACTOR_COLS:
            wide[key][mg] = r.get("Factor Value")
    return [wide[k] for k in order]
