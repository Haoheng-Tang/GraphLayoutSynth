"""Optional Claude-based interpretation for ranked candidate reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
LLM_SYSTEM_PROMPT = (
    "You are an architectural layout graph evaluator. "
    "Interpret structured candidate reports. "
    "Do not invent metrics. "
    "Do not replace the deterministic ranking. "
    "Clearly distinguish measured metrics from your interpretation."
)


class LlmEvaluationError(RuntimeError):
    """Raised when optional LLM evaluation cannot run."""


def load_llm_environment(env_path: str = ".env.local") -> None:
    """Load environment variables from .env.local if available."""
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_candidate_evaluation_prompt(
    ranking_report: dict | list[dict],
    candidate_reports: list[dict],
) -> str:
    """Build a prompt for interpreting ranked graph-layout candidates."""
    payload = {
        "ranking_report": ranking_report,
        "candidate_reports": candidate_reports,
    }
    structured_data = json.dumps(payload, indent=2, sort_keys=True)
    return (
        "Interpret the supplied GraphLayoutSynth candidate ranking reports.\n\n"
        "Your output must include these sections:\n"
        "1. Overall summary.\n"
        "2. Top candidate interpretation.\n"
        "3. Comparison of top-k candidates.\n"
        "4. Major strengths.\n"
        "5. Major weaknesses.\n"
        "6. Likely repair suggestions.\n"
        "7. Note that deterministic ranking remains primary.\n"
        "8. Warning that this interpretation is not a validity certificate.\n\n"
        "Rules:\n"
        "- Use only the supplied reports.\n"
        "- Do not invent metrics.\n"
        "- Do not assume geometric information that is not present.\n"
        "- Do not claim code-level correctness.\n"
        "- Do not certify building-code or life-safety compliance.\n"
        "- Clearly distinguish measured metrics from interpretation.\n"
        "- Explain tradeoffs in graph-layout terms.\n\n"
        "Structured reports:\n"
        f"{structured_data}\n"
    )


def _extract_message_text(message: Any) -> str:
    content = getattr(message, "content", [])
    text_parts = []
    for block in content:
        block_type = getattr(block, "type", None)
        block_text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type")
            block_text = block.get("text")
        if block_type == "text" and block_text:
            text_parts.append(str(block_text))
    return "\n".join(text_parts).strip()


def evaluate_candidates_with_claude(
    ranking_report: dict | list[dict],
    candidate_reports: list[dict],
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = 1200,
) -> str:
    """Call Claude and return a markdown evaluation string."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LlmEvaluationError(
            "ANTHROPIC_API_KEY is missing. Add it to .env.local or set it in the environment."
        )

    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise LlmEvaluationError(
            "The optional Anthropic SDK is not installed. Install with: python -m pip install -e \".[llm]\""
        ) from exc

    prompt = build_candidate_evaluation_prompt(ranking_report, candidate_reports)
    client = Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=LLM_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )
    text = _extract_message_text(message)
    if not text:
        raise LlmEvaluationError("Claude response did not contain text content.")
    return text


def _read_json(path: str | Path) -> dict | list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_candidates_with_llm(
    ranking_report_path: str,
    candidate_report_paths: list[str] | None = None,
    model: str | None = None,
    output_path: str | None = None,
    env_path: str = ".env.local",
    max_tokens: int = 1200,
) -> dict:
    """Read reports, call Claude, optionally save markdown output, and return metadata."""
    load_llm_environment(env_path)
    ranking_report = _read_json(ranking_report_path)
    candidate_reports = [
        _read_json(path)
        for path in (candidate_report_paths or [])
    ]
    selected_model = model or DEFAULT_CLAUDE_MODEL
    markdown = evaluate_candidates_with_claude(
        ranking_report,
        candidate_reports,
        model=selected_model,
        max_tokens=max_tokens,
    )

    saved_output_path = None
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        saved_output_path = str(path)

    return {
        "model": selected_model,
        "ranking_report_path": str(ranking_report_path),
        "candidate_report_paths": candidate_report_paths or [],
        "output_path": saved_output_path,
        "markdown": markdown,
    }
