"""Bounded real-backend read checks with a deliberately redacted JSON report.

Run explicitly after login. This module never calls write endpoints, retries,
prints response objects, or stores reference tokens in its report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_anything.rednote import __version__
from cli_anything.rednote.core.backend import Backend
from cli_anything.rednote.core.errors import RedNoteError
from cli_anything.rednote.core.refs import parse_reference

_CASE_NAMES = ("health", "login", "search", "note", "me", "unread", "notifications", "profile")
_SAFE_ERROR_CODES = {
    "INVALID_BACKEND_URL", "INVALID_TIMEOUT", "INVALID_BACKEND_TOKEN",
    "BACKEND_TIMEOUT", "BACKEND_UNAVAILABLE", "BACKEND_UNAUTHORIZED",
    "BACKEND_RATE_LIMITED", "BACKEND_HTTP_ERROR", "INVALID_BACKEND_RESPONSE",
    "BACKEND_ERROR", "INVALID_REFERENCE", "INVALID_REFERENCE_KIND",
    "MISSING_XSEC_TOKEN", "INVALID_XSEC_TOKEN", "INVALID_KEYWORD", "INVALID_FILTER",
    "INVALID_ARGUMENT",
}
_FAILED = object()


class _CheckFailure(Exception):
    def __init__(self, code: str):
        self.code = code


def _require(condition: bool) -> None:
    # Avoid pytest-style assertion expansion exposing a live response.
    if not condition:
        raise _CheckFailure("RESPONSE_SHAPE_MISMATCH")


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nullable_list(value: Any) -> bool:
    return value is None or isinstance(value, list)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_report() -> dict:
    return {
        "schema_version": 1,
        "cli_version": __version__,
        "started_at": _timestamp(),
        "dependencies": {"backend": "unknown", "login": "unknown"},
        "cases": [],
    }


def _finish(report: dict) -> dict:
    counts = {status: sum(case["status"] == status for case in report["cases"])
              for status in ("passed", "failed", "not_run")}
    report["finished_at"] = _timestamp()
    report["summary"] = {
        **counts,
        "complete": counts["failed"] == 0 and counts["not_run"] == 0,
        "outcome": "failed" if counts["failed"] else "partial" if counts["not_run"] else "passed",
    }
    return report


def _failure_fields(error: Exception) -> dict:
    if isinstance(error, _CheckFailure):
        return {"error_code": error.code}
    if isinstance(error, RedNoteError):
        code = error.code if isinstance(error.code, str) and error.code in _SAFE_ERROR_CODES else "REDNOTE_ERROR"
        return {"error_code": code}
    name = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        name = "Exception"
    return {"error_code": "UNEXPECTED_EXCEPTION", "exception_type": name}


def _health(value: Any) -> dict:
    _require(isinstance(value, dict))
    _require(value.get("status") == "healthy" and value.get("service") == "xiaohongshu-mcp")
    return {}


def _login(value: Any) -> dict:
    _require(isinstance(value, dict) and isinstance(value.get("is_logged_in"), bool))
    if value["is_logged_in"] is not True:
        raise _CheckFailure("NOT_AUTHENTICATED")
    return {}


def _search(value: Any) -> dict:
    _require(isinstance(value, dict) and isinstance(value.get("feeds"), list))
    _require(_integer(value.get("count")) and value["count"] == len(value["feeds"]))
    return {"returned_notes": len(value["feeds"])}


def _note(value: Any, expected_id: str) -> dict:
    _require(isinstance(value, dict) and value.get("feed_id") == expected_id)
    detail = value.get("data")
    _require(isinstance(detail, dict) and isinstance(detail.get("note"), dict))
    note = detail["note"]
    _require(note.get("noteId") == expected_id)
    _require(isinstance(note.get("title"), str) and isinstance(note.get("desc"), str))
    comments = detail.get("comments")
    _require(isinstance(comments, dict) and "list" in comments and _nullable_list(comments.get("list")))
    _require(isinstance(comments.get("hasMore"), bool))
    return {"returned_comments": len(comments.get("list") or [])}


def _profile(value: Any) -> dict:
    _require(isinstance(value, dict) and isinstance(value.get("data"), dict))
    profile = value["data"]
    _require(isinstance(profile.get("userBasicInfo"), dict))
    basic_info = profile["userBasicInfo"]
    _require(any(_nonblank_string(basic_info.get(key)) for key in ("nickname", "redId")))
    _require("feeds" in profile and "interactions" in profile)
    _require(_nullable_list(profile.get("feeds")) and _nullable_list(profile.get("interactions")))
    return {"returned_notes": len(profile.get("feeds") or [])}


def _unread(value: Any) -> dict:
    _require(isinstance(value, dict) and isinstance(value.get("data"), dict))
    counts = value["data"]
    _require(all(_integer(counts.get(key)) for key in ("mentions", "likes", "connections", "unread")))
    return {key: counts[key] for key in ("mentions", "likes", "connections", "unread")}


def _notifications(value: Any) -> dict:
    _require(isinstance(value, dict) and isinstance(value.get("data"), dict))
    notifications = value["data"]
    _require(notifications.get("tab") == "mentions")
    _require(_integer(notifications.get("filtered")))
    _require(isinstance(notifications.get("items"), list) and len(notifications["items"]) <= 1)
    _require(all(isinstance(item, dict) and _nonblank_string(item.get("id"))
                 for item in notifications["items"]))
    return {"returned_items": len(notifications["items"]), "filtered_items": notifications["filtered"]}


def _reference(identifier: Any, token: Any, kind: str) -> tuple[str, str] | None:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", identifier) or not isinstance(token, str):
        return None
    try:
        return parse_reference(identifier, kind=kind, xsec_token=token)
    except RedNoteError:
        return None


def run_read_smoke(client: Any, *, query: str = "咖啡", profile_reference: str | None = None) -> dict:
    """Perform at most eight sequential read calls and return only safe metadata.

    Health or login failure stops subsequent account calls. Later independent
    reads continue once each; missing note/profile references are not_run.
    """
    report = _new_report()
    cases = report["cases"]
    interrupted_case = None

    def execute(name, call, validate, dependencies):
        nonlocal interrupted_case
        if interrupted_case is not None:
            not_run(name, "run_interrupted", [interrupted_case])
            return _FAILED
        case = {"name": name, "depends_on": dependencies}
        try:
            value = call()
            counts = validate(value)
        except KeyboardInterrupt:
            interrupted_case = name
            case.update(status="failed", error_code="INTERRUPTED")
            cases.append(case)
            return _FAILED
        except Exception as error:  # noqa: BLE001 -- Redact all unexpected live errors.
            case.update(status="failed", **_failure_fields(error))
            cases.append(case)
            return _FAILED
        case["status"] = "passed"
        if counts:
            case["counts"] = counts
        cases.append(case)
        return value

    def not_run(name, reason, dependencies):
        cases.append({"name": name, "status": "not_run", "reason": reason, "depends_on": dependencies})

    health = execute("health", client.health, _health, [])
    if health is _FAILED:
        report["dependencies"]["backend"] = "unavailable"
        for name in _CASE_NAMES[1:]:
            not_run(name, "backend_check_failed", ["health"])
        return _finish(report)
    report["dependencies"]["backend"] = "available"
    login = execute("login", client.login_status, _login, ["health"])
    if login is _FAILED:
        report["dependencies"]["login"] = "unverified"
        for name in _CASE_NAMES[2:]:
            not_run(name, "login_not_verified", ["login"])
        return _finish(report)
    report["dependencies"]["login"] = "authenticated"

    search = execute("search", lambda: client.search(query), _search, ["login"])
    note_reference = None
    if search is not _FAILED:
        for row in search["feeds"]:
            if isinstance(row, dict) and row.get("modelType", "note") == "note":
                note_reference = _reference(row.get("id"), row.get("xsecToken"), "note")
                if note_reference is not None:
                    break
    if note_reference is None:
        not_run("note", "search_failed" if search is _FAILED else "no_usable_note_reference", ["search"])
    else:
        note_id, note_token = note_reference
        execute("note", lambda: client.note(note_id, note_token, load_all_comments=False),
                lambda value: _note(value, note_id), ["search"])

    execute("me", client.me, _profile, ["login"])
    execute("unread", client.notifications_unread, _unread, ["login"])
    notifications = execute("notifications", lambda: client.notifications(tab="mentions", limit=1),
                            _notifications, ["login"])

    if interrupted_case is not None:
        not_run("profile", "run_interrupted", [interrupted_case])
        return _finish(report)
    user_reference = None
    if profile_reference is not None:
        try:
            user_reference = parse_reference(profile_reference, kind="user")
        except Exception as error:  # noqa: BLE001 -- Never expose live reference text.
            cases.append({"name": "profile", "status": "failed", "depends_on": ["profile_reference"],
                          **_failure_fields(error)})
            return _finish(report)
    elif notifications is not _FAILED:
        for item in notifications["data"]["items"]:
            author = item.get("from")
            if isinstance(author, dict):
                user_reference = _reference(author.get("user_id"), author.get("xsec_token"), "user")
                if user_reference is not None:
                    break
    if user_reference is None:
        not_run("profile", "no_independent_profile_reference", ["profile_reference"])
    else:
        user_id, user_token = user_reference
        dependency = "profile_reference" if profile_reference is not None else "notifications"
        execute("profile", lambda: client.profile(user_id, user_token), _profile, ["login", dependency])
    return _finish(report)


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded authenticated reads and save a redacted report.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query", default="咖啡")
    args = parser.parse_args(argv)
    try:
        with Backend(
            os.getenv("REDNOTE_BACKEND_URL", "http://127.0.0.1:18060"),
            timeout=float(os.getenv("REDNOTE_TIMEOUT", "120")),
            token=os.getenv("REDNOTE_BACKEND_TOKEN"),
        ) as client:
            report = run_read_smoke(client, query=args.query, profile_reference=os.getenv("REDNOTE_LIVE_USER_URL"))
    except KeyboardInterrupt:
        report = _new_report()
        report["cases"].append({"name": "health", "status": "failed", "error_code": "INTERRUPTED", "depends_on": []})
        for name in _CASE_NAMES[1:]:
            report["cases"].append({"name": name, "status": "not_run", "reason": "run_interrupted", "depends_on": ["health"]})
        _finish(report)
    except Exception as error:  # noqa: BLE001 -- Configuration errors may include secrets.
        report = _new_report()
        report["dependencies"]["backend"] = "unavailable"
        report["cases"].append({"name": "health", "status": "failed", "depends_on": [], **_failure_fields(error)})
        for name in _CASE_NAMES[1:]:
            report["cases"].append({"name": name, "status": "not_run", "reason": "backend_setup_failed", "depends_on": ["health"]})
        _finish(report)
    try:
        _write_report(args.output, report)
    except OSError:
        print(json.dumps({"error_code": "REPORT_WRITE_FAILED"}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    if report["summary"]["failed"]:
        return 1
    return 0 if report["summary"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
