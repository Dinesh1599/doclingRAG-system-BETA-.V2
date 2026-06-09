"""Domain + numeric-integrity gates (universal schema)."""

from rate_filing.quarantine import Quarantine
from rate_filing.validators import (validate_factor_values,
                                     validate_vehicle_symbols, validate_zip)


def test_mbi_domain_quarantines_bad_codes():
    q = Quarantine()
    rows = [{"Symbol Type": "MBI", "Symbol": "01", "Source Page": "263"},
            {"Symbol Type": "MBI", "Symbol": "Prohibited", "Source Page": "263"},
            {"Symbol Type": "MBI", "Symbol": "99", "Source Page": "264"}]
    ok = validate_vehicle_symbols(rows, q)
    assert len(ok) == 2
    assert len(q) == 1 and "99" in q.items[0].reason


def test_liability_symbol_must_be_nonempty():
    q = Quarantine()
    rows = [{"Symbol Type": "Liability", "Symbol": "G", "Source Page": "510"},
            {"Symbol Type": "Liability", "Symbol": "", "Source Page": "511"}]
    ok = validate_vehicle_symbols(rows, q)
    assert len(ok) == 1 and len(q) == 1


def test_zip_must_be_five_digits():
    q = Quarantine()
    rows = [{"ZIP Code": "07042", "Rating Territory": 5},
            {"ZIP Code": "704", "Rating Territory": 5}]
    ok = validate_zip(rows, q)
    assert len(ok) == 1 and len(q) == 1


def test_factor_value_range_flags_outliers():
    q = Quarantine()
    rows = [{"Factor Value": "0.99", "Source Page": "316"},
            {"Factor Value": "9999", "Source Page": "316"}]
    validate_factor_values(rows, q, lo=0.0, hi=100.0)
    assert len(q) == 1 and "9999" in q.items[0].detail
