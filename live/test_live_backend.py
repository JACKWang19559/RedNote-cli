"""Explicit real service verification. Unavailable service/login is a failure."""

import os

from cli_anything.rednote.core.backend import Backend
from cli_anything.rednote.core.refs import parse_reference


def test_authenticated_read_workflow():
    with Backend(
        os.getenv("REDNOTE_BACKEND_URL", "http://127.0.0.1:18060"),
        timeout=float(os.getenv("REDNOTE_TIMEOUT", "60")),
        token=os.getenv("REDNOTE_BACKEND_TOKEN"),
    ) as client:
        assert client.health()
        assert client.login_status()["is_logged_in"] is True, "扫码登录后重跑 live 测试"
        result = client.search(os.getenv("REDNOTE_LIVE_QUERY", "咖啡"))
        assert isinstance(result.get("feeds"), list)
        assert isinstance(client.me().get("data"), dict)
        # Optional references extend the same live run without pretending that
        # an arbitrary search result carries a valid user-profile token.
        note_url = os.getenv("REDNOTE_LIVE_NOTE_URL")
        if note_url:
            note_id, token = parse_reference(note_url)
            assert client.note(note_id, token).get("feed_id") == note_id
        user_url = os.getenv("REDNOTE_LIVE_USER_URL")
        if user_url:
            user_id, token = parse_reference(user_url, kind="user")
            assert isinstance(client.profile(user_id, token).get("data"), dict)
