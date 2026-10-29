import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from aider_walkthrough import approved_export, digest, prepare, read_json
from aider_walkthrough.exporter import _git, read_text, write_new


def git(repo: Path, *args: str) -> str:
    return (
        subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.autocrlf=false",
                "-C",
                str(repo),
                *args,
            ]
        )
        .decode("utf-8")
        .strip()
    )


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    file = repo / "private.py"
    file.write_text("before = 1\n", encoding="utf-8")
    git(repo, "add", "private.py")
    git(repo, "commit", "-m", "Initial")
    file.write_text("after = 'TEST_SECRET'\n", encoding="utf-8")
    git(repo, "add", "private.py")
    git(repo, "commit", "-m", "Change")
    commit = git(repo, "rev-parse", "HEAD")
    history = tmp_path / "history.md"
    history.write_text(
        "# aider chat started at fixture\n\n#### Change this\n\n"
        "TEST_SECRET and Straße\n",
        encoding="utf-8",
    )
    plan = dict(
        version=1,
        title="Session",
        provenance="Synthetic test fixture",
        checkpoints=[
            dict(id="one", title="Change", commit=commit, history_lines=[3, 5])
        ],
    )
    return repo, history, plan


def test_real_git_patch_and_explicit_excerpt(source: tuple) -> None:
    repo, history, plan = source
    review = prepare(repo, history, plan)
    checkpoint = review["payload"]["checkpoints"][0]
    assert checkpoint["excerpt"] == "#### Change this\n\nTEST_SECRET and Straße"
    assert "-before = 1" in checkpoint["patch"]
    assert "+after = 'TEST_SECRET'" in checkpoint["patch"]
    assert checkpoint["parent"] == git(repo, "rev-parse", "HEAD^")
    assert review["sha256"] == digest(review["payload"])


def test_source_worktree_index_head_and_untracked_unchanged(source: tuple) -> None:
    repo, history, plan = source
    (repo / "private.py").write_text("uncommitted change\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked", encoding="utf-8")
    status = git(repo, "status", "--porcelain")
    index = (repo / ".git/index").read_bytes()
    head = git(repo, "rev-parse", "HEAD")
    history_bytes = history.read_bytes()
    prepare(repo, history, plan)
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "status", "--porcelain") == status
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / "private.py").read_text() == "uncommitted change\n"
    assert (repo / "untracked.txt").read_text() == "untracked"
    assert history.read_bytes() == history_bytes


def test_redacts_transcript_excerpt_patch_and_paths(source: tuple) -> None:
    repo, history, plan = source
    plan["title"] = "TEST_SECRET walkthrough"
    review = prepare(repo, history, plan, ["TEST_SECRET", "private.py"])
    raw = json.dumps(review, ensure_ascii=False)
    assert "TEST_SECRET" not in raw and "private.py" not in raw
    assert "[REDACTED]" in raw
    assert review["payload"]["redaction_count"] >= 5
    assert "Straße" in review["payload"]["transcript"]


def test_overlapping_literal_redactions_are_longest_first(source: tuple) -> None:
    repo, history, plan = source
    review = prepare(repo, history, plan, ["TEST", "TEST_SECRET"])
    assert "[REDACTED]_SECRET" not in json.dumps(review)
    assert "TEST_SECRET" not in json.dumps(review)


@pytest.mark.parametrize("redactions", [[""], ["a\nb"], ["a\rb"], [3]])
def test_invalid_redactions(source: tuple, redactions: list) -> None:
    with pytest.raises(ValueError, match="Redactions"):
        prepare(*source, redactions)


@pytest.mark.parametrize(
    "bounds", [[0, 1], [3, 2], [1, 999], [True, 2], [1.5, 3], [1], "1:3", None]
)
def test_invalid_line_bounds(source: tuple, bounds: object) -> None:
    repo, history, plan = source
    plan["checkpoints"][0]["history_lines"] = bounds
    with pytest.raises(ValueError, match="history_lines"):
        prepare(repo, history, plan)


@pytest.mark.parametrize(
    "sha", ["HEAD", "HEAD~1", "--output=bad", "abc", "A" * 40, "0" * 40, "a" * 41, ""]
)
def test_invalid_or_missing_commits(source: tuple, sha: str) -> None:
    repo, history, plan = source
    plan["checkpoints"][0]["commit"] = sha
    with pytest.raises(ValueError):
        prepare(repo, history, plan)


def test_blob_is_not_a_commit(source: tuple) -> None:
    repo, history, plan = source
    plan["checkpoints"][0]["commit"] = git(repo, "rev-parse", "HEAD:private.py")
    with pytest.raises(ValueError, match="not a commit"):
        prepare(repo, history, plan)


def test_root_commit_supported(source: tuple) -> None:
    repo, history, plan = source
    plan["checkpoints"][0]["commit"] = git(repo, "rev-list", "--max-parents=0", "HEAD")
    checkpoint = prepare(repo, history, plan)["payload"]["checkpoints"][0]
    assert checkpoint["parent"] is None
    assert "+before = 1" in checkpoint["patch"]


def test_merge_commit_rejected(source: tuple) -> None:
    repo, history, plan = source
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("feature")
    git(repo, "add", "feature.txt")
    git(repo, "commit", "-m", "Feature")
    git(repo, "checkout", "main")
    (repo / "main.txt").write_text("main")
    git(repo, "add", "main.txt")
    git(repo, "commit", "-m", "Main")
    git(repo, "merge", "--no-ff", "--no-edit", "feature")
    plan["checkpoints"][0]["commit"] = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="Merge"):
        prepare(repo, history, plan)


def test_external_diff_and_textconv_are_not_executed(
    source: tuple, tmp_path: Path
) -> None:
    repo, history, plan = source
    marker = tmp_path / "executed"
    script = tmp_path / "unexpected.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
    )
    command = f'"{sys.executable}" "{script}"'
    git(repo, "config", "diff.external", command)
    git(repo, "config", "diff.evil.textconv", command)
    (repo / ".gitattributes").write_text("*.py diff=evil\n")
    review = prepare(repo, history, plan)
    assert "+after = 'TEST_SECRET'" in review["payload"]["checkpoints"][0]["patch"]
    assert not marker.exists()
    # Negative control: the configured converter really is executable; the
    # exporter succeeds above specifically because it disables textconv.
    git(repo, "show", "--no-ext-diff", "--textconv", "--format=", "HEAD")
    assert marker.exists()


def test_crlf_normalization(source: tuple) -> None:
    repo, history, plan = source
    history.write_bytes(
        history.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
    )
    payload = prepare(repo, history, plan)["payload"]
    assert "\r" not in payload["transcript"]
    assert payload["checkpoints"][0]["excerpt"].startswith("#### Change this\n")


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"hello\x00world"])
def test_nontext_history_rejected(source: tuple, raw: bytes) -> None:
    repo, history, plan = source
    history.write_bytes(raw)
    with pytest.raises((UnicodeError, ValueError)):
        prepare(repo, history, plan)


def test_explicit_ack_and_edited_review(source: tuple) -> None:
    review = prepare(*source)
    with pytest.raises(ValueError, match="acknowledgement"):
        approved_export(review, "")
    assert approved_export(review, review["sha256"]) == review
    review["payload"]["title"] = "Edited"
    with pytest.raises(ValueError, match="acknowledgement"):
        approved_export(review, review["sha256"])
    result = approved_export(review, digest(review["payload"]))
    assert result["payload"]["title"] == "Edited"


def test_independent_digest_oracle() -> None:
    payload = {"z": [None, 3], "a": {"x": "ß", "b": False}}
    expected = hashlib.sha256(
        '{"a":{"b":false,"x":"ß"},"z":[null,3]}'.encode()
    ).hexdigest()
    assert digest(payload) == expected


def test_reject_nonfinite_json_digest() -> None:
    with pytest.raises(ValueError):
        digest({"value": float("nan")})


def test_exclusive_output_creation(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    write_new(path, {"test": "Straße"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_new(path, {"other": True})
    assert path.read_bytes() == before


def test_replacement_objects_do_not_change_selected_patch(source: tuple) -> None:
    repo, history, plan = source
    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    git(repo, "replace", plan["checkpoints"][0]["commit"], root)
    # Negative control: ordinary Git now shows the replacement object's patch.
    assert "+before = 1" in git(repo, "show", "--format=", "HEAD")
    patch = prepare(repo, history, plan)["payload"]["checkpoints"][0]["patch"]
    assert "+after = 'TEST_SECRET'" in patch


def test_redaction_cannot_silently_invalidate_structural_sha(source: tuple) -> None:
    repo, history, plan = source
    with pytest.raises(ValueError, match="commit SHA"):
        prepare(repo, history, plan, [plan["checkpoints"][0]["commit"]])


def test_literal_patterns_are_not_regex_or_recursive_replacements(
    source: tuple,
) -> None:
    repo, history, plan = source
    history.write_text("a.*b\n[REDACTED]\n", encoding="utf-8", newline="\n")
    plan["checkpoints"][0]["history_lines"] = [1, 2]
    payload = prepare(repo, history, plan, ["a.*b", "REDACTED"])["payload"]
    assert payload["transcript"] == "[REDACTED]\n[[REDACTED]]\n"
    assert "[REDACTED]" in payload["checkpoints"][0]["excerpt"]


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(mapping="inferred"),
        lambda p: p.update(redaction_count=True),
        lambda p: p.update(redaction_count=-1),
        lambda p: p.update(title=""),
        lambda p: p.update(extra="hidden data"),
        lambda p: p["checkpoints"][0].update(excerpt="wrong lines"),
        lambda p: p["checkpoints"][0].update(commit="short"),
        lambda p: p["checkpoints"][0].update(parent="short"),
        lambda p: p["checkpoints"][0].update(history_lines=[1, 999]),
        lambda p: p["checkpoints"][0].update(patch=None),
        lambda p: p["checkpoints"][0].update(patch="x\n" * 10000),
    ],
)
def test_review_edits_must_preserve_valid_format(source: tuple, change: object) -> None:
    review = prepare(*source)
    change(review["payload"])
    with pytest.raises(ValueError):
        approved_export(review, digest(review["payload"]))


def test_formatted_output_limit_checked_before_creating_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aider_walkthrough import exporter

    monkeypatch.setattr(exporter, "MAX_BYTES", 20)
    target = tmp_path / "oversized.json"
    with pytest.raises(ValueError, match="Formatted export"):
        write_new(target, {"text": "a" * 30})
    assert not target.exists()


@pytest.mark.parametrize("raw", ["[]", "false", "null", '"text"'])
def test_json_root_must_be_object(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "input.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="object"):
        read_json(path)


def test_read_limit_and_git_output_limit(source: tuple, tmp_path: Path) -> None:
    path = tmp_path / "large"
    path.write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="exceeds"):
        read_text(path, limit=10)
    with pytest.raises(ValueError, match="export limit"):
        _git(source[0], "show", "--format=", "HEAD", limit=10)


@pytest.mark.parametrize("version", [None, 0, 2, True, "1"])
def test_unsupported_version(source: tuple, version: object) -> None:
    repo, history, plan = source
    plan["version"] = version
    with pytest.raises(ValueError, match="version"):
        prepare(repo, history, plan)


def test_duplicate_checkpoint_ids(source: tuple) -> None:
    repo, history, plan = source
    plan["checkpoints"].append(dict(plan["checkpoints"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(repo, history, plan)


def test_empty_checkpoint_list(source: tuple) -> None:
    repo, history, plan = source
    plan["checkpoints"] = []
    with pytest.raises(ValueError, match="checkpoints"):
        prepare(repo, history, plan)


def test_cli_prepare_inspect_export_and_wrong_ack(
    source: tuple, tmp_path: Path
) -> None:
    repo, history, plan = source
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    review_path = tmp_path / "review.json"
    out = tmp_path / "export.json"
    base = [sys.executable, "-m", "aider_walkthrough"]
    subprocess.run(
        [
            *base,
            "prepare",
            "--repo",
            str(repo),
            "--history",
            str(history),
            "--plan",
            str(plan_path),
            "--out",
            str(review_path),
        ],
        check=True,
    )
    inspection = json.loads(
        subprocess.check_output([*base, "inspect", "--review", str(review_path)])
    )
    wrong = subprocess.run(
        [
            *base,
            "export",
            "--review",
            str(review_path),
            "--ack-sha256",
            "wrong",
            "--out",
            str(out),
        ],
        capture_output=True,
    )
    assert wrong.returncode == 1 and not out.exists()
    subprocess.run(
        [
            *base,
            "export",
            "--review",
            str(review_path),
            "--ack-sha256",
            inspection["sha256"],
            "--out",
            str(out),
        ],
        check=True,
    )
    assert (
        read_json(out)["payload"]["checkpoints"][0]["commit"]
        == plan["checkpoints"][0]["commit"]
    )
