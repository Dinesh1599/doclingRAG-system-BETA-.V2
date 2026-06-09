"""OpenAI client helpers using JSON-schema structured outputs.

The LLM is used only for semantic work (classify, map labels->columns, convert
prose to parameters). It must never transcribe numbers; deterministic code
copies cell values. Every call returns a typed object validated against a
JSON schema.
"""

import json
from functools import lru_cache

from openai import OpenAI

from .config import Config


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    cfg = Config.from_env()
    cfg.require_openai()
    return OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)


def structured(model: str, system: str, user: str,
               schema: dict, schema_name: str, temperature: float = 0.0) -> dict:
    """Call the model and return a dict validated against `schema`."""
    resp = _client().chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    return json.loads(resp.choices[0].message.content)
