"""Runtime configuration, loaded from environment (.env supported)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_base_url: str
    model_extract: str
    model_classify: str
    docling_do_ocr: bool
    docling_table_mode: str          # "accurate" | "fast"
    docling_ocr_engine: str          # "ocrmac" | "easyocr" | "rapidocr" | "tesseract"
    docling_num_threads: int
    docling_ocr_min_chars: int       # page text shorter than this may be scanned
    docling_ocr_min_image_coverage: float  # ...and image covering >= this -> OCR
    database_url: str                # postgres + pgvector store ("" disables RAG)
    embed_model: str                 # OpenAI embeddings model (1536-dim)
    chunk_max_chars: int             # target chunk size when splitting a page
    input_dir: Path
    output_dir: Path
    processed_dir: Path              # relevant PDFs moved here after processing
    skipped_dir: Path                # irrelevant PDFs moved here

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model_extract=os.environ.get("LLM_MODEL_EXTRACT", "gpt-4o"),
            model_classify=os.environ.get("LLM_MODEL_CLASSIFY", "gpt-4o-mini"),
            docling_do_ocr=os.environ.get("DOCLING_DO_OCR", "true").lower() != "false",
            docling_table_mode=os.environ.get("DOCLING_TABLE_MODE", "fast"),
            docling_ocr_engine=os.environ.get("DOCLING_OCR_ENGINE", "auto"),
            docling_num_threads=int(os.environ.get("DOCLING_NUM_THREADS", "4")),
            docling_ocr_min_chars=int(os.environ.get("DOCLING_OCR_MIN_CHARS", "50")),
            docling_ocr_min_image_coverage=float(
                os.environ.get("DOCLING_OCR_MIN_IMAGE_COVERAGE", "0.5")),
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://rag:rag@localhost:5436/rag"),
            embed_model=os.environ.get("EMBED_MODEL", "text-embedding-3-small"),
            chunk_max_chars=int(os.environ.get("CHUNK_MAX_CHARS", "800")),
            input_dir=Path(os.environ.get("INPUT_DIR", "./input")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            processed_dir=Path(os.environ.get("PROCESSED_DIR", "./processed")),
            skipped_dir=Path(os.environ.get("SKIPPED_DIR", "./skipped")),
        )

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
