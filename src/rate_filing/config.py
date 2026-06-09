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
    docling_serve_url: str
    input_dir: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model_extract=os.environ.get("LLM_MODEL_EXTRACT", "gpt-4o"),
            model_classify=os.environ.get("LLM_MODEL_CLASSIFY", "gpt-4o-mini"),
            docling_serve_url=os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001"),
            input_dir=Path(os.environ.get("INPUT_DIR", "./input")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
