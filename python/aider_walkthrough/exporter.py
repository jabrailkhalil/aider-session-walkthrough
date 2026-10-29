from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_PATCH_BYTES = 2 * 1024 * 1024
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def read_text(path: Path, limit: int = MAX_BYTES) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Input exceeds {limit} bytes")
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError("NUL bytes are unsupported")
    return text


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(read_text(path))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str, limit: int = MAX_PATCH_BYTES) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "--no-lazy-fetch",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo),
                *args,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git exceeded the 30-second read timeout") from exc
    if result.returncode:
        raise ValueError("Git could not read the selected commit")
    if len(result.stdout) > limit:
        raise ValueError("Git output exceeds the export limit")
    return result.stdout.decode("utf-8")


def _text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 10000
        or "\x00" in value
    ):
        raise ValueError(f"{name} must be nonempty text, at most 10000 characters")
    return value


def validate_payload(payload: dict[str, Any]) -> None:
    """Validate the portable v1 format after redaction or review edits."""
    expected = {
        "version",
        "title",
        "provenance",
        "mapping",
        "transcript",
        "redaction_count",
        "checkpoints",
    }
    if set(payload) != expected:
        raise ValueError("Unexpected or missing payload fields")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported payload version")
    if payload["mapping"] != "explicit":
        raise ValueError("Mapping must be explicit")
    _text(payload["title"], "title")
    _text(payload["provenance"], "provenance")
    transcript = payload["transcript"]
    if not isinstance(transcript, str) or "\x00" in transcript or "\r" in transcript:
        raise ValueError("Transcript must be LF-normalized text")
    lines = transcript.split("\n")
    count = payload["redaction_count"]
    if type(count) is not int or not 0 <= count <= 2**53 - 1:
        raise ValueError("Invalid redaction count")
    checkpoints = payload["checkpoints"]
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 50:
        raise ValueError("Expected 1–50 checkpoints")
    seen = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "id",
            "title",
            "commit",
            "parent",
            "history_lines",
            "excerpt",
            "patch",
        }:
            raise ValueError("Invalid checkpoint fields")
        identifier = _text(checkpoint["id"], "checkpoint ID")
        _text(checkpoint["title"], "checkpoint title")
        if identifier in seen:
            raise ValueError("Duplicate checkpoint ID")
        seen.add(identifier)
        for name in ("commit", "parent"):
            value = checkpoint[name]
            if name == "parent" and value is None:
                continue
            if not isinstance(value, str) or not SHA.fullmatch(value):
                raise ValueError("Invalid commit SHA after review/redaction")
        bounds = checkpoint["history_lines"]
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(type(n) is not int for n in bounds)
            or not 1 <= bounds[0] <= bounds[1] <= len(lines)
        ):
            raise ValueError("Invalid history_lines after review/redaction")
        if checkpoint["excerpt"] != "\n".join(lines[bounds[0] - 1 : bounds[1]]):
            raise ValueError("Excerpt does not match the reviewed transcript")
        if not isinstance(checkpoint["patch"], str) or "\x00" in checkpoint["patch"]:
            raise ValueError("Patch must be text")
        if (
            len(checkpoint["patch"].encode("utf-8")) > MAX_PATCH_BYTES
            or checkpoint["patch"].count("\n") >= 10000
        ):
            raise ValueError("Patch exceeds 2 MiB or 10000 lines")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Combined export exceeds 5 MiB")


def prepare(
    repo: Path,
    history: Path,
    plan: dict[str, Any],
    redactions: list[str] | None = None,
) -> dict[str, Any]:
    """Read Git/history, construct a review payload, never modify the source repo.

    The plan's author explicitly associates transcript line ranges and commits.
    No attribution is inferred from Markdown headings, timestamps or subjects.
    """
    if type(plan.get("version")) is not int or plan.get("version") != 1:
        raise ValueError("Unsupported plan version")
    title = _text(plan.get("title"), "title")
    provenance = _text(plan.get("provenance"), "provenance")
    steps = plan.get("checkpoints")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise ValueError("Expected 1–50 checkpoints")
    transcript = read_text(history).replace("\r\n", "\n").replace("\r", "\n")
    lines = transcript.split("\n")
    checkpoints = []
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Each checkpoint must be an object")
        identifier = _text(step.get("id"), "checkpoint ID")
        if identifier in seen:
            raise ValueError("Duplicate checkpoint ID")
        seen.add(identifier)
        commit = step.get("commit")
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError("Use a full lowercase commit SHA")
        if _git(repo, "cat-file", "-t", commit).strip() != "commit":
            raise ValueError("Selected object is not a commit")
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit, "--").split()
        if len(parents) > 2:
            raise ValueError(
                "Merge commits are unsupported; select a single-parent commit"
            )
        bounds = step.get("history_lines")
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(type(n) is not int for n in bounds)
            or not 1 <= bounds[0] <= bounds[1] <= len(lines)
        ):
            raise ValueError("history_lines must be a valid inclusive 1-based range")
        patch = _git(
            repo,
            "show",
            "--format=",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-color",
            "--unified=3",
            commit,
            "--",
        )
        checkpoints.append(
            dict(
                id=identifier,
                title=_text(step.get("title"), "checkpoint title"),
                commit=commit,
                parent=parents[1] if len(parents) == 2 else None,
                history_lines=bounds,
                excerpt="\n".join(lines[bounds[0] - 1 : bounds[1]]),
                patch=patch,
            )
        )
    if any(
        not isinstance(s, str) or not s or len(s) > 10000 or "\n" in s or "\r" in s
        for s in (redactions or [])
    ):
        raise ValueError("Redactions must be nonempty literal strings")
    replacements = sorted(set(redactions or []), key=len, reverse=True)
    pattern = (
        re.compile("|".join(re.escape(s) for s in replacements))
        if replacements
        else None
    )
    count = 0

    def redact(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            if pattern:
                value, replaced = pattern.subn("[REDACTED]", value)
                count += replaced
            return value
        if isinstance(value, list):
            return [redact(v) for v in value]
        if isinstance(value, dict):
            return {k: redact(v) for k, v in value.items()}
        return value

    payload = redact(
        dict(
            version=1,
            title=title,
            provenance=provenance,
            mapping="explicit",
            transcript=transcript,
            checkpoints=checkpoints,
        )
    )
    payload["redaction_count"] = count
    validate_payload(payload)
    return {"payload": payload, "sha256": digest(payload)}


def approved_export(review: dict[str, Any], acknowledgement: str) -> dict[str, Any]:
    """Require acknowledgement of the exact current payload, including edits."""
    payload = review.get("payload")
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        raise ValueError("Invalid review payload")
    validate_payload(payload)
    actual = digest(payload)
    if acknowledgement != actual:
        raise ValueError("Review acknowledgement does not match the current payload")
    return {"payload": payload, "sha256": actual}


def write_new(path: Path, value: dict[str, Any]) -> None:
    # Exclusive creation prevents accidental overwrite of an existing export.
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Formatted export exceeds 5 MiB")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
