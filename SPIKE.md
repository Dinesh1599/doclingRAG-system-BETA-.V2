# Spike result — MBI / VLR vehicle tables

**Question:** Can Docling's table parser produce correct row counts for the two
large vehicle tables (target: MBI 23,402; VLR 21,067)?

**Answer: No — use the coordinate-based fallback for sheets 19 & 20.**

Evidence (docling-serve `to_formats=json`, `table_mode=accurate`, 10 MBI pages):
- Docling returned ~63–73 rows/page; golden average is ~94.7 MBI / ~88.9 VLR per page.
- Docling **merges adjacent vehicle rows** into one (e.g. `"A MARTIN A ROMEO"`,
  `"Prohibited Prohibited"` = Aston Martin Vanquish + Alfa Romeo 4C collapsed).
- One page in the slice produced **no table at all**.

Coordinate check (pdfplumber word clustering by y-position):
- MBI p263–266: ~96 text-rows/page; VLR p510: ~92 — matches golden once the
  per-page header row is dropped.
- The vehicle pages are **digital text** (no OCR needed): single-column layout,
  fixed column x-anchors (Year≈52, Make≈85, Model≈187, Body≈334, Engine≈372,
  4WD≈464, MBI/Symbol≈490).

**Decision:** Docling remains the single table parser for all other sheets.
Sheets 19 (MBI) and 20 (VLR) use a deterministic pdfplumber coordinate parser
(`extractors/vehicle_tables.py`).
