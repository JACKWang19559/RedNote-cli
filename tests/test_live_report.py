"""Read-smoke report tests use only an in-memory client, never a live account."""

import copy
import json
from urllib.parse import quote

import pytest

from cli_anything.rednote.core.errors import RedNoteError
from live import read_smoke

NOTE_ID = "680000000000000000000001"
USER_ID = "580000000000000000000002"
NOTIFICATION_ID = "680000000000000000000003"
NOTE_TOKEN = "SECRET_NOTE+/="
USER_TOKEN = "SECRET_PROFILE+/="
PRIVATE_TEXT = "PRIVATE_ACCOUNT_TEXT"
PROFILE_URL = f"https://www.xiaohongshu.com/user/profile/{USER_ID}?xsec_token={quote(USER_TOKEN, safe='')}"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.values = {
            "health": {"service": "xiaohongshu-mcp", "status": "healthy", "version": PRIVATE_TEXT},
            "login_status": {"is_logged_in": True, "username": PRIVATE_TEXT},
            "search": {"count": 1, "feeds": [{"modelType": "note", "id": NOTE_ID, "xsecToken": NOTE_TOKEN}]},
            "note": {"feed_id": NOTE_ID, "data": {
                "note": {"noteId": NOTE_ID, "title": PRIVATE_TEXT, "desc": PRIVATE_TEXT},
                "comments": {"list": None, "hasMore": False},
            }},
            "me": {"data": {"userBasicInfo": {"nickname": PRIVATE_TEXT}, "feeds": None, "interactions": None}},
            "notifications_unread": {"data": {"mentions": 2, "likes": 1, "connections": 0, "unread": 3}},
            "notifications": {"data": {"tab": "mentions", "filtered": 0, "items": [{
                "id": NOTIFICATION_ID, "comment_text": PRIVATE_TEXT,
                "from": {"user_id": USER_ID, "xsec_token": USER_TOKEN, "nickname": PRIVATE_TEXT},
            }]}},
            "profile": {"data": {"userBasicInfo": {"nickname": PRIVATE_TEXT}, "feeds": [], "interactions": []}},
        }

    def __getattr__(self, name):
        if name not in self.values:
            raise AssertionError("A write or unknown endpoint was requested")

        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.values[name]
            if isinstance(value, BaseException):
                raise value
            return copy.deepcopy(value)
        return call

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def case(report, name):
    return next(item for item in report["cases"] if item["name"] == name)


def assert_redacted(report):
    encoded = json.dumps(report)
    for private in (
        NOTE_ID, USER_ID, NOTIFICATION_ID, NOTE_TOKEN, USER_TOKEN, PRIVATE_TEXT,
        PROFILE_URL, "https://", "xsecToken", "username",
    ):
        assert private not in encoded
    assert '"data"' not in encoded


def test_success_is_bounded_sequential_and_uses_independent_profile_token():
    client = FakeClient()
    report = read_smoke.run_read_smoke(client, query=PRIVATE_TEXT)
    assert [entry[0] for entry in client.calls] == [
        "health", "login_status", "search", "note", "me", "notifications_unread", "notifications", "profile",
    ]
    assert client.calls[3] == ("note", (NOTE_ID, NOTE_TOKEN), {"load_all_comments": False})
    assert client.calls[6] == ("notifications", (), {"tab": "mentions", "limit": 1})
    assert client.calls[7] == ("profile", (USER_ID, USER_TOKEN), {})
    assert report["summary"] == {"passed": 8, "failed": 0, "not_run": 0, "complete": True, "outcome": "passed"}
    assert case(report, "me")["counts"] == {"returned_notes": 0}
    assert case(report, "note")["counts"] == {"returned_comments": 0}
    assert_redacted(report)


def test_health_failure_stops_before_account_calls():
    client = FakeClient()
    client.values["health"] = {"status": "healthy", "service": "wrong-service"}
    report = read_smoke.run_read_smoke(client)
    assert len(client.calls) == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["not_run"] == 7
    assert case(report, "health")["error_code"] == "RESPONSE_SHAPE_MISMATCH"
    assert_redacted(report)


def test_not_logged_in_is_failure_and_stops_reads():
    client = FakeClient()
    client.values["login_status"] = {"is_logged_in": False, "username": PRIVATE_TEXT}
    report = read_smoke.run_read_smoke(client)
    assert [entry[0] for entry in client.calls] == ["health", "login_status"]
    assert case(report, "login")["error_code"] == "NOT_AUTHENTICATED"
    assert report["summary"]["not_run"] == 6
    assert_redacted(report)


def test_missing_note_and_profile_are_not_run_instead_of_passed():
    client = FakeClient()
    client.values["search"] = {"count": 0, "feeds": []}
    client.values["notifications"] = {"data": {"tab": "mentions", "filtered": 0, "items": []}}
    report = read_smoke.run_read_smoke(client)
    assert case(report, "note")["status"] == "not_run"
    assert case(report, "profile")["status"] == "not_run"
    assert report["summary"] == {"passed": 6, "failed": 0, "not_run": 2, "complete": False, "outcome": "partial"}
    assert len(client.calls) == 6
    assert_redacted(report)


def test_interrupted_read_preserves_prior_results_and_stops_remaining_calls():
    client = FakeClient()
    client.values["note"] = KeyboardInterrupt()
    report = read_smoke.run_read_smoke(client)
    assert [entry[0] for entry in client.calls] == ["health", "login_status", "search", "note"]
    assert case(report, "note")["error_code"] == "INTERRUPTED"
    assert report["summary"] == {"passed": 3, "failed": 1, "not_run": 4, "complete": False, "outcome": "failed"}
    assert_redacted(report)


def test_search_error_preserves_independent_checks_and_does_not_retry():
    client = FakeClient()
    client.values["search"] = RedNoteError("BACKEND_TIMEOUT", PRIVATE_TEXT, PROFILE_URL)
    report = read_smoke.run_read_smoke(client)
    assert case(report, "search")["error_code"] == "BACKEND_TIMEOUT"
    assert case(report, "note")["status"] == "not_run"
    assert case(report, "me")["status"] == "passed"
    assert case(report, "profile")["status"] == "passed"
    assert len([entry for entry in client.calls if entry[0] == "search"]) == 1
    assert_redacted(report)


@pytest.mark.parametrize("response", [
    {"feed_id": NOTE_ID, "data": {"note": {"noteId": USER_ID}}},
    {"feed_id": NOTE_ID, "data": None},
])
def test_echoed_note_id_alone_does_not_prove_detail_success(response):
    client = FakeClient()
    client.values["note"] = response
    report = read_smoke.run_read_smoke(client)
    assert case(report, "note")["error_code"] == "RESPONSE_SHAPE_MISMATCH"
    assert_redacted(report)


@pytest.mark.parametrize("endpoint,response", [
    ("login_status", {"is_logged_in": "true"}),
    ("search", {"count": 1, "feeds": []}),
    ("me", {"userBasicInfo": {"nickname": PRIVATE_TEXT}}),
    ("notifications_unread", {"data": {"mentions": True, "likes": 0, "connections": 0, "unread": 1}}),
    ("notifications", {"data": {"tab": "likes", "filtered": 0, "items": []}}),
])
def test_malformed_shapes_fail_without_dumping_responses(endpoint, response):
    client = FakeClient()
    client.values[endpoint] = response
    report = read_smoke.run_read_smoke(client)
    name = {"login_status": "login", "notifications_unread": "unread"}.get(endpoint, endpoint)
    assert case(report, name)["status"] == "failed"
    assert case(report, name)["error_code"] == "RESPONSE_SHAPE_MISMATCH"
    assert_redacted(report)


@pytest.mark.parametrize("endpoint", ["me", "profile"])
@pytest.mark.parametrize("basic_info", [
    {},
    {"nickname": "", "redId": " \t "},
    {"nickname": None, "redId": 123},
])
def test_empty_or_invalid_profile_identity_is_not_reported_as_success(endpoint, basic_info):
    client = FakeClient()
    client.values[endpoint] = {"data": {"userBasicInfo": basic_info, "feeds": None, "interactions": None}}
    report = read_smoke.run_read_smoke(client)
    assert case(report, endpoint)["status"] == "failed"
    assert case(report, endpoint)["error_code"] == "RESPONSE_SHAPE_MISMATCH"
    assert report["summary"]["outcome"] == "failed"
    assert not report["summary"]["complete"]
    assert_redacted(report)


def test_profile_red_id_alone_is_sufficient_and_stays_redacted():
    client = FakeClient()
    for endpoint in ("me", "profile"):
        client.values[endpoint]["data"]["userBasicInfo"] = {"nickname": "", "redId": PRIVATE_TEXT}
    report = read_smoke.run_read_smoke(client)
    assert report["summary"]["complete"]
    assert_redacted(report)


@pytest.mark.parametrize("item", [{}, {"id": ""}, {"id": " \t "}, {"id": 123}])
def test_notification_without_valid_id_is_not_reported_as_success(item):
    client = FakeClient()
    client.values["notifications"]["data"]["items"] = [item]
    report = read_smoke.run_read_smoke(client)
    assert case(report, "notifications")["status"] == "failed"
    assert case(report, "notifications")["error_code"] == "RESPONSE_SHAPE_MISMATCH"
    assert report["summary"]["outcome"] == "failed"
    assert not report["summary"]["complete"]
    assert_redacted(report)


def test_notification_id_does_not_require_optional_fields_or_hex_format():
    client = FakeClient()
    client.values["notifications"]["data"]["items"] = [{"id": PRIVATE_TEXT}]
    report = read_smoke.run_read_smoke(client)
    assert case(report, "notifications")["status"] == "passed"
    assert case(report, "profile")["status"] == "not_run"
    assert_redacted(report)


def test_explicit_profile_reference_is_used_when_notifications_are_empty():
    client = FakeClient()
    client.values["notifications"] = {"data": {"tab": "mentions", "filtered": 0, "items": []}}
    report = read_smoke.run_read_smoke(client, profile_reference=PROFILE_URL)
    assert case(report, "profile")["status"] == "passed"
    assert client.calls[-1] == ("profile", (USER_ID, USER_TOKEN), {})
    assert_redacted(report)


def test_missing_notification_profile_token_is_not_replaced_with_note_token():
    client = FakeClient()
    del client.values["notifications"]["data"]["items"][0]["from"]["xsec_token"]
    report = read_smoke.run_read_smoke(client)
    assert case(report, "profile")["status"] == "not_run"
    assert not any(entry[0] == "profile" for entry in client.calls)
    assert_redacted(report)


def test_unexpected_exception_and_unknown_error_code_are_redacted():
    client = FakeClient()
    client.values["me"] = RuntimeError(PRIVATE_TEXT + PROFILE_URL)
    client.values["notifications_unread"] = RedNoteError(PRIVATE_TEXT, PRIVATE_TEXT, PROFILE_URL)
    report = read_smoke.run_read_smoke(client)
    assert case(report, "me")["exception_type"] == "RuntimeError"
    assert case(report, "unread")["error_code"] == "REDNOTE_ERROR"
    assert_redacted(report)


@pytest.mark.parametrize("outcome,expected_code,failed,not_run", [
    ("passed", 0, 0, 0),
    ("partial", 2, 0, 1),
    ("failed", 1, 1, 0),
])
def test_main_exit_code_matches_redacted_report_outcome(
    tmp_path, monkeypatch, capsys, outcome, expected_code, failed, not_run,
):
    client = FakeClient()
    if outcome == "failed":
        client.values["note"] = RuntimeError(PRIVATE_TEXT)
    monkeypatch.setattr(read_smoke, "Backend", lambda *args, **kwargs: client)
    monkeypatch.setenv("REDNOTE_BACKEND_TOKEN", PRIVATE_TEXT)
    monkeypatch.setenv("REDNOTE_TIMEOUT", "120")
    if outcome == "partial":
        monkeypatch.delenv("REDNOTE_LIVE_USER_URL", raising=False)
        client.values["notifications"]["data"]["items"] = []
    else:
        monkeypatch.setenv("REDNOTE_LIVE_USER_URL", PROFILE_URL)
    output = tmp_path / "reports" / "live.json"
    code = read_smoke.main(["--output", str(output), "--query", PRIVATE_TEXT])
    assert code == expected_code
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["failed"] == failed
    assert report["summary"]["not_run"] == not_run
    assert report["summary"]["outcome"] == outcome
    assert report["summary"]["complete"] == (outcome == "passed")
    assert_redacted(report)
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == report


def test_main_invalid_configuration_saves_static_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REDNOTE_TIMEOUT", PRIVATE_TEXT)
    output = tmp_path / "report.json"
    assert read_smoke.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert case(report, "health")["exception_type"] == "ValueError"
    assert report["summary"]["not_run"] == 7
    assert_redacted(report)
    assert_redacted(json.loads(capsys.readouterr().out))
