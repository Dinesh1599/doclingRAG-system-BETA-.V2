"""Quarantine report: rows that fail validation or are low-confidence.

Nothing is silently dropped or guessed; flagged rows land here for human review.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QuarantineItem:
    sheet: str
    reason: str
    source_page: str = ""
    detail: str = ""


@dataclass
class Quarantine:
    items: list[QuarantineItem] = field(default_factory=list)

    def add(self, sheet: str, reason: str, source_page="", detail="") -> None:
        self.items.append(QuarantineItem(sheet, reason, str(source_page), detail))

    def __len__(self) -> int:
        return len(self.items)

    def write_csv(self, path: Path) -> Path | None:
        if not self.items:
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["sheet", "reason", "source_page", "detail"])
            for it in self.items:
                w.writerow([it.sheet, it.reason, it.source_page, it.detail])
        return path
