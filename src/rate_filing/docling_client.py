"""Local Docling conversion — runs the `docling` package in-process.

Replaces the old docling-serve Docker container + HTTP API. The conversion now
happens inside the uv environment (`.venv`); on Apple Silicon docling can use the
native MPS backend, which the CPU Docker image could not.

We export the DoclingDocument with `export_to_dict()`, which yields the SAME JSON
shape docling-serve returned (top-level `texts` / `tables` / `pages`), so
`doc_model.normalize()` and the on-disk cache `<pdf>.docling.json` are unchanged.
"""

import sys
from pathlib import Path


class DoclingError(RuntimeError):
    pass


class DoclingConverter:
    """In-process DoclingDocument producer. Importing docling (torch + models)
    is heavy, so the underlying converter is built lazily on first use."""

    def __init__(self, *, do_ocr: bool = True, table_mode: str = "fast",
                 ocr_engine: str = "auto", num_threads: int | None = None) -> None:
        self.do_ocr = do_ocr
        self.table_mode = table_mode
        self.ocr_engine = ocr_engine
        self.num_threads = num_threads
        self._converter = None

    def _resolve_engine(self) -> str:
        """'auto' -> Apple Vision on macOS (native, no model download), EasyOCR
        elsewhere (cross-platform, GPU-capable)."""
        if self.ocr_engine.lower() != "auto":
            return self.ocr_engine.lower()
        return "ocrmac" if sys.platform == "darwin" else "easyocr"

    def _ocr_options(self):
        """Pick the OCR backend by name (after resolving 'auto')."""
        from docling.datamodel.pipeline_options import (
            EasyOcrOptions, OcrMacOptions, RapidOcrOptions, TesseractCliOcrOptions)
        engines = {
            "ocrmac": OcrMacOptions,
            "easyocr": EasyOcrOptions,
            "rapidocr": RapidOcrOptions,
            "tesseract": TesseractCliOcrOptions,
        }
        name = self._resolve_engine()
        factory = engines.get(name)
        if factory is None:
            raise DoclingError(
                f"unknown DOCLING_OCR_ENGINE {self.ocr_engine!r}; "
                f"choose 'auto' or one of {sorted(engines)}")
        return factory()

    def _ensure(self) -> None:
        if self._converter is not None:
            return
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                AcceleratorDevice, AcceleratorOptions, PdfPipelineOptions,
                TableFormerMode)
            from docling.document_converter import (DocumentConverter,
                                                    PdfFormatOption)
        except ImportError as e:  # docling not installed
            raise DoclingError(
                "docling package not installed in this environment. "
                "Run: uv add docling") from e

        opts = PdfPipelineOptions()
        opts.do_ocr = self.do_ocr
        if self.do_ocr:
            opts.ocr_options = self._ocr_options()
        opts.do_table_structure = True
        opts.table_structure_options.mode = (
            TableFormerMode.ACCURATE if self.table_mode == "accurate"
            else TableFormerMode.FAST)
        # AUTO picks MPS on Apple Silicon, CUDA on NVIDIA, else CPU.
        opts.accelerator_options = AcceleratorOptions(
            num_threads=self.num_threads or 4, device=AcceleratorDevice.AUTO)
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    def health(self) -> bool:
        """True if docling is importable/usable (no network service to ping)."""
        try:
            self._ensure()
            return True
        except DoclingError:
            return False

    def convert_file(self, file_path: Path) -> dict:
        """Convert one PDF and return the DoclingDocument as a dict (same shape
        docling-serve's json_content had)."""
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        self._ensure()
        result = self._converter.convert(str(file_path))
        doc = getattr(result, "document", None)
        if doc is None:
            raise DoclingError(f"docling returned no document for {file_path.name}")
        return doc.export_to_dict()


def _smoke(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m rate_filing.docling_client <file.pdf>", file=sys.stderr)
        return 2
    conv = DoclingConverter()
    if not conv.health():
        print("docling not installed; run: uv add docling", file=sys.stderr)
        return 1
    doc = conv.convert_file(Path(argv[1]))
    print("texts:", len(doc.get("texts", [])), "tables:", len(doc.get("tables", [])),
          "pages:", len(doc.get("pages", {}) or doc.get("pages", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke(sys.argv))
