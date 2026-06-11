"""OpenAI client helpers using JSON-schema structured outputs.

Single chokepoint for every LLM call: relevance triage, filing-identity
detection, and bill-pay extraction. For bill-pay the model does transcribe the
numbers it reads, with the per-row Accuracy Score as the safeguard. Every call
returns a typed object validated against a JSON schema.
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


def embed(model: str, texts: list[str], batch_size: int = 256) -> list[list[float]]:
    """Embed texts with an OpenAI embeddings model, batched. Returns one vector
    per input, in order."""
    client = _client()
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out


def chat(model: str, system: str, user: str, temperature: float = 0.2) -> str:
    """Plain-text chat completion (used by the RAG chat agent)."""
    resp = _client().chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


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
