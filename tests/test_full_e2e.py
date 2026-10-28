"""Installed executable and actual local-file workflows, without network I/O."""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest


def _resolve_cli(name):
    """Prefer this Python environment's installed command; enforce for release tests."""
    scripts = sysconfig.get_path("scripts")
    path = shutil.which(name, path=scripts) or shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1":
        raise RuntimeError(
            f"{name} not found. Install with: python -m pip install -e ."
        )
    print(f"[_resolve_cli] Falling back to Python module for {name}")
    return [sys.executable, "-m", "cli_anything.rednote.rednote_cli"]


@pytest.fixture
def isolated_process_env(tmp_path, monkeypatch):
    # The subprocess inherits an arbitrary directory, never a repository-specific cwd.
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    env = os.environ.copy()
    env["REDNOTE_HOME"] = str(tmp_path / "草稿 数据")
    env["PYTHONIOENCODING"] = "utf-8"
    for key in (
        "REDNOTE_BACKEND_URL",
        "REDNOTE_BACKEND_TOKEN",
        "REDNOTE_TIMEOUT",
        "PYTHONPATH",
    ):
        env.pop(key, None)
    return env


def run_cli(name, args, env, *, input=None):
    return subprocess.run(
        _resolve_cli(name) + args,
        env=env,
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


def run_json(args, env, *, expected=0, name="rednote"):
    result = run_cli(name, ["--json", *args], env)
    assert result.returncode == expected, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ok"] is (expected == 0)
    return parsed["data"] if expected == 0 else parsed["error"]


@pytest.mark.parametrize("name", ["rednote", "cli-anything-rednote"])
def test_installed_aliases_help_and_json_info(name, isolated_process_env):
    help_result = run_cli(name, ["--help"], isolated_process_env)
    assert help_result.returncode == 0, help_result.stderr
    assert "draft" in help_result.stdout
    assert "search" in help_result.stdout
    info = run_json(["info"], isolated_process_env, name=name)
    assert info["name"] == "cli-anything-rednote"
    assert Path(info["home"]) == Path(isolated_process_env["REDNOTE_HOME"])
    assert Path(info["skill_path"]).is_file()
    assert not Path(info["home"]).exists()


def test_installed_unicode_draft_workflow_and_exports(isolated_process_env, tmp_path):
    body = tmp_path / "正文 文件.txt"
    body.write_text("你好小红书\n第二行 ☕", encoding="utf-8")
    env = isolated_process_env
    created = run_json(
        [
            "draft",
            "create",
            "--title",
            "周末探店",
            "--content-file",
            str(body),
            "--tag",
            "咖啡",
            "--image",
            r"C:\相片\封面 图.jpg",
        ],
        env,
    )
    draft_id = created["id"]
    assert created["content"] == body.read_text(encoding="utf-8")
    assert run_json(["draft", "show", draft_id], env) == created
    changed = run_json(["draft", "update", draft_id, "--content", "更新后的内容"], env)
    assert changed["content"] == "更新后的内容"
    run_json(["draft", "undo"], env)
    assert run_json(["draft", "show", draft_id], env) == created
    run_json(["draft", "redo"], env)
    assert run_json(["draft", "show", draft_id], env) == changed

    json_file, md_file = tmp_path / "结果.json", tmp_path / "结果.md"
    run_json(
        ["draft", "export", draft_id, "--format", "json", "--output", str(json_file)],
        env,
    )
    run_json(["draft", "export", draft_id, "--output", str(md_file)], env)
    assert json.loads(json_file.read_text(encoding="utf-8")) == changed
    markdown = md_file.read_text(encoding="utf-8")
    assert "# 周末探店" in markdown
    assert "更新后的内容" in markdown
    assert "#咖啡" in markdown
    assert r"C:\相片\封面 图.jpg" in markdown
    assert (
        (Path(env["REDNOTE_HOME"]) / "drafts.sqlite3")
        .read_bytes()
        .startswith(b"SQLite format 3\x00")
    )
    print(f"\nJSON artifact: {json_file} ({json_file.stat().st_size} bytes)")
    print(f"Markdown artifact: {md_file} ({md_file.stat().st_size} bytes)")


def test_installed_delete_undo_and_redo_invalidation(isolated_process_env):
    env = isolated_process_env
    draft = run_json(["draft", "create", "--title", "可恢复草稿"], env)
    run_json(["draft", "delete", draft["id"]], env)
    error = run_json(["draft", "show", draft["id"]], env, expected=1)
    assert error["code"] == "DRAFT_NOT_FOUND"
    run_json(["draft", "undo"], env)
    assert run_json(["draft", "show", draft["id"]], env) == draft
    run_json(["draft", "update", draft["id"], "--content", "新分支"], env)
    assert run_json(["draft", "redo"], env, expected=1)["code"] == "DRAFT_HISTORY_EMPTY"


def test_installed_usage_errors_are_json(isolated_process_env):
    error = run_json(["unknown"], isolated_process_env, expected=2)
    assert error["code"] == "USAGE_ERROR"


def test_installed_piped_repl_preserves_unicode_windows_paths(isolated_process_env):
    commands = 'draft create --title "中文 草稿" --image "C:\\图片 目录\\封面.png"\ndraft list\nquit\n'
    result = run_cli(
        "cli-anything-rednote", ["--json", "repl"], isolated_process_env, input=commands
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(outputs) == 2
    created = outputs[0]["data"]
    assert created["title"] == "中文 草稿"
    assert created["images"] == [r"C:\图片 目录\封面.png"]
    assert outputs[1]["data"] == [created]
