"""Filing profile: optional per-carrier page-range HINTS (LEGACY / unused).

DEPRECATED as the primary mechanism. The pipeline now discovers section
locations from page content (see discover.py), so nothing is hardcoded. This
remains only as an optional override structure and for reference; pipeline.run()
no longer reads it.
"""

from dataclasses import dataclass, field


@dataclass
class FilingProfile:
    # 1-based inclusive PDF page ranges
    mbi: tuple[int, int] = (263, 509)
    vlr: tuple[int, int] = (510, 746)
    territory_defs: tuple[int, int] = (143, 147)
    general_rules: tuple[int, int] = (3, 13)       # scanned / OCR
    installments: tuple[int, int] = (4, 8)         # scanned / OCR
    docling_pages: tuple[int, int] = (1, 262)      # parsed via docling-serve
    extra: dict = field(default_factory=dict)


GEICO_NJ = FilingProfile()
