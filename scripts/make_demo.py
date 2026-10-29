"""Make a curated history fixture and real Git commits without model calls."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from aider_walkthrough.exporter import approved_export, prepare

ROOT = Path(__file__).resolve().parents[1]
workspace = ROOT / "demo-work"
workspace.mkdir(exist_ok=True)
env = dict(
    os.environ,
    GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00",
    GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00",
    GIT_AUTHOR_NAME="Demo",
    GIT_COMMITTER_NAME="Demo",
    GIT_AUTHOR_EMAIL="demo@example.invalid",
    GIT_COMMITTER_EMAIL="demo@example.invalid",
)


def git(repo: Path, *args: str) -> str:
    return (
        subprocess.check_output(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.filemode=false",
                "-c",
                "user.name=Demo",
                "-c",
                "user.email=demo@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                f"core.hooksPath={repo / '.git' / 'disabled-hooks'}",
                "-C",
                str(repo),
                *args,
            ],
            env=env,
        )
        .decode("utf-8")
        .strip()
    )


with tempfile.TemporaryDirectory(prefix="fixture-", dir=workspace) as tmp:
    repo = Path(tmp).resolve()
    assert repo.is_relative_to(workspace.resolve())
    git(repo, "init", "-b", "main")
    code = repo / "normalize.py"
    code.write_text(
        "def normalize(value):\n    return value\n", encoding="utf-8", newline="\n"
    )
    git(repo, "add", "normalize.py")
    git(repo, "commit", "-m", "Initial normalization fixture")
    code.write_text(
        "# Fixture token: DEMO_TOKEN_NEVER_USE\n"
        "def normalize(value):\n    return value.strip()\n",
        encoding="utf-8",
        newline="\n",
    )
    git(repo, "add", "normalize.py")
    git(repo, "commit", "-m", "Trim surrounding whitespace")
    first = git(repo, "rev-parse", "HEAD")
    code.write_text(
        "# Fixture token: DEMO_TOKEN_NEVER_USE\n"
        "def normalize(value):\n    return value.strip().casefold()\n",
        encoding="utf-8",
        newline="\n",
    )
    git(repo, "add", "normalize.py")
    git(repo, "commit", "-m", "Normalize Unicode case")
    second = git(repo, "rev-parse", "HEAD")
    # This format follows Aider's user_input/ai_output history representation.
    # The content is authored specifically for this deterministic example.
    transcript = (
        "# aider chat started at 2026-01-01 00:00:00\n\n"
        "#### Trim leading and trailing spaces in normalize().\n\n"
        "I changed normalize() to call strip().\n"
        "Fixture token: DEMO_TOKEN_NEVER_USE\n\n"
        "#### Make comparison case-insensitive, including Straße.\n\n"
        "I added casefold() after strip(). This maps Straße to strasse.\n"
    )
    history = repo / "history.md"
    history.write_text(transcript, encoding="utf-8", newline="\n")
    plan = dict(
        version=1,
        title="A small normalization change",
        provenance=(
            "Curated demo fixture with real Git commits; not a live model session."
        ),
        checkpoints=[
            dict(
                id="trim",
                title="Trim surrounding whitespace",
                commit=first,
                history_lines=[3, 6],
            ),
            dict(
                id="case",
                title="Normalize Unicode case",
                commit=second,
                history_lines=[8, 10],
            ),
        ],
    )
    review = prepare(repo, history, plan, ["DEMO_TOKEN_NEVER_USE"])
    # This acknowledgement is for the known test fixture, not a claim of
    # independent human review of a real session.
    export = approved_export(review, review["sha256"])
    assert "DEMO_TOKEN_NEVER_USE" not in json.dumps(export)
    (ROOT / "public").mkdir(exist_ok=True)
    (ROOT / "public" / "session.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Generated", len(export["payload"]["checkpoints"]), "checkpoints.")
