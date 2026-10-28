import pytest

from cli_anything.rednote.core.errors import RedNoteError
from cli_anything.rednote.core.refs import parse_reference

ID = "680000000000000000000001"


@pytest.mark.parametrize("path,kind", [("explore", "note"), ("discovery/item", "note"), ("user/profile", "user")])
def test_full_urls_and_percent_encoding(path, kind):
    assert parse_reference(f"https://www.xiaohongshu.com/{path}/{ID}?xsec_token=ab%2Bcd%2F%25%3D&xsec_source=pc_search", kind) == (ID, "ab+cd/%=")


def test_token_is_not_double_decoded():
    assert parse_reference(f"https://xiaohongshu.com/explore/{ID}?xsec_token=ab%252B")[1] == "ab%2B"


def test_explicit_token_overrides_url():
    assert parse_reference(f"https://xiaohongshu.com/explore/{ID}?xsec_token=stale", xsec_token="fresh+==") == (ID, "fresh+==")


def test_raw_id_requires_explicit_token():
    assert parse_reference(ID, xsec_token="abc/==") == (ID, "abc/==")
    with pytest.raises(RedNoteError) as exc:
        parse_reference(ID)
    assert exc.value.code == "MISSING_XSEC_TOKEN"


@pytest.mark.parametrize("url,kind", [
    (f"https://xiaohongshu.com.evil.example/explore/{ID}?xsec_token=a", "note"),
    (f"https://evil.example/?url=https://xiaohongshu.com/explore/{ID}?xsec_token=a", "note"),
    (f"https://www.xiaohongshu.com@evil.example/explore/{ID}?xsec_token=a", "note"),
    (f"http://xiaohongshu.com/explore/{ID}?xsec_token=a", "note"),
    (f"https://xiaohongshu.com:444/explore/{ID}?xsec_token=a", "note"),
    (f"https://xiaohongshu.com/user/profile/{ID}?xsec_token=a", "note"),
    (f"https://xiaohongshu.com/explore/{ID}?xsec_token=a", "user"),
    (f"https://xiaohongshu.com/explore/{ID}/other?xsec_token=a", "note"),
    (f"https://xiaohongshu.com/explore/../{ID}?xsec_token=a", "note"),
    ("https://xhslink.com/a/xyz", "note"),
    ("https://[bad", "note"),
    ("not-an-id", "note"),
    ("", "note"),
])
def test_wrong_links_are_rejected(url, kind):
    with pytest.raises(RedNoteError) as exc:
        parse_reference(url, kind=kind)
    assert exc.value.code == "INVALID_REFERENCE"


@pytest.mark.parametrize("query", ["", "xsec_token=", "xsec_source=pc_feed"])
def test_missing_url_token(query):
    with pytest.raises(RedNoteError) as exc:
        parse_reference(f"https://xiaohongshu.com/explore/{ID}?{query}")
    assert exc.value.code == "MISSING_XSEC_TOKEN"


def test_duplicate_url_token_rejected():
    with pytest.raises(RedNoteError) as exc:
        parse_reference(f"https://xiaohongshu.com/explore/{ID}?xsec_token=a&xsec_token=b")
    assert exc.value.code == "INVALID_REFERENCE"


@pytest.mark.parametrize("token", ["has space", "has\nnewline", "\x00token", "has\x7f"])
def test_token_control_characters_rejected(token):
    with pytest.raises(RedNoteError) as exc:
        parse_reference(ID, xsec_token=token)
    assert exc.value.code == "INVALID_XSEC_TOKEN"


def test_unescaped_plus_is_invalid_in_query():
    # Standard query decoding turns '+' into a space. Accept %2B instead.
    with pytest.raises(RedNoteError) as exc:
        parse_reference(f"https://xiaohongshu.com/explore/{ID}?xsec_token=a+b")
    assert exc.value.code == "INVALID_XSEC_TOKEN"
