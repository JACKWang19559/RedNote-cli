"""Local draft workflows, persisted history, and failure cases."""

import json
import sqlite3

import pytest

from cli_anything.rednote.core.drafts import DraftStore
from cli_anything.rednote.core.errors import RedNoteError


def test_lifecycle_and_unicode_round_trip(tmp_path):
    store = DraftStore(tmp_path / "中文 数据")
    draft = store.create(
        "上海咖啡地图 ☕",
        "第一行\n第二行",
        [" 咖啡 ", "#探店", "咖啡"],
        ["C:/照片/拿铁.jpg"],
    )
    assert draft["tags"] == ["咖啡", "探店"]
    assert store.list() == [draft]
    assert DraftStore(store.home).get(draft["id"]) == draft
    updated = store.update(
        draft["id"], title="新的标题", content="", tags=[], images=[]
    )
    assert updated["created_at"] == draft["created_at"]
    assert updated["content"] == ""
    assert updated["tags"] == updated["images"] == []
    assert store.delete(draft["id"]) == updated
    assert store.list() == []
    with pytest.raises(RedNoteError):
        store.get(draft["id"])


def test_history_persists_across_process_like_store_instances(tmp_path):
    original = DraftStore(tmp_path).create("初稿", "正文")
    changed = DraftStore(tmp_path).update(original["id"], title="修改")
    DraftStore(tmp_path).delete(original["id"])

    assert DraftStore(tmp_path).undo() == {
        "action": "undo",
        "id": original["id"],
        "draft": changed,
    }
    assert DraftStore(tmp_path).undo()["draft"] == original
    assert DraftStore(tmp_path).undo()["draft"] is None
    assert DraftStore(tmp_path).list() == []
    with pytest.raises(RedNoteError):
        DraftStore(tmp_path).undo()

    assert DraftStore(tmp_path).redo()["draft"] == original
    assert DraftStore(tmp_path).redo()["draft"] == changed
    assert DraftStore(tmp_path).redo()["draft"] is None
    with pytest.raises(RedNoteError):
        DraftStore(tmp_path).redo()


def test_new_edit_discards_redo_branch(tmp_path):
    store = DraftStore(tmp_path)
    first = store.create("第一个")
    second = store.create("第二个")
    store.undo()
    replacement = store.update(first["id"], content="新分支")
    with pytest.raises(RedNoteError):
        store.redo()
    with pytest.raises(RedNoteError):
        store.get(second["id"])
    assert store.get(first["id"]) == replacement
    assert store.undo()["draft"] == first


def test_noop_update_preserves_redo_and_history(tmp_path):
    store = DraftStore(tmp_path)
    original = store.create("初稿")
    changed = store.update(original["id"], content="更新")
    store.undo()
    assert store.update(original["id"]) == original
    assert store.redo()["draft"] == changed


@pytest.mark.parametrize(
    "fields",
    [
        {"title": ""},
        {"title": "  "},
        {"title": "字" * 21},
        {"title": "两\n行"},
        {"title": None},
        {"title": "正常", "content": "字" * 1001},
        {"title": "正常", "tags": "not-a-list"},
        {"title": "正常", "tags": [""]},
        {"title": "正常", "tags": ["###"]},
        {"title": "正常", "tags": [42]},
        {"title": "正常", "images": [None]},
        {"title": "正常", "images": [""]},
    ],
)
def test_invalid_draft_does_not_mutate_store(tmp_path, fields):
    store = DraftStore(tmp_path)
    with pytest.raises(RedNoteError):
        store.create(**fields)
    assert store.list() == []
    with pytest.raises(RedNoteError):
        store.undo()


def test_limits_and_failed_update_are_atomic(tmp_path):
    store = DraftStore(tmp_path)
    draft = store.create("字" * 20, "😊" * 1000)
    with pytest.raises(RedNoteError):
        store.update(draft["id"], title="字" * 21)
    assert store.get(draft["id"]) == draft
    assert store.undo()["draft"] is None


def test_missing_ids_and_sql_injection_are_not_records(tmp_path):
    store = DraftStore(tmp_path)
    draft = store.create("正常")
    for action in (store.get, store.update, store.delete):
        with pytest.raises(RedNoteError):
            action("' OR 1=1 --")
    assert store.list() == [draft]


def test_export_json_markdown_and_existing_file(tmp_path):
    store = DraftStore(tmp_path / "data")
    draft = store.create("猫咪日常", "今天晒太阳", ["宠物"], ["图片 一.jpg"])
    exported = store.export(draft["id"], tmp_path / "草稿.json", format="json")
    assert json.loads((tmp_path / "草稿.json").read_text(encoding="utf-8")) == draft
    assert exported["path"] == str((tmp_path / "草稿.json").resolve())
    store.export(draft["id"], tmp_path / "草稿.md")
    markdown = (tmp_path / "草稿.md").read_text(encoding="utf-8")
    assert "# 猫咪日常" in markdown
    assert "今天晒太阳" in markdown
    assert "#宠物" in markdown
    assert "![图片 1](<图片 一.jpg>)" in markdown
    with pytest.raises(RedNoteError):
        store.export(draft["id"], tmp_path / "草稿.md")
    assert (tmp_path / "草稿.md").read_text(encoding="utf-8") == markdown


def test_bad_export_does_not_create_file_or_change_history(tmp_path):
    store = DraftStore(tmp_path / "data")
    draft = store.create("草稿")
    with pytest.raises(RedNoteError):
        store.export(draft["id"], tmp_path / "bad", format="html")
    assert not (tmp_path / "bad").exists()
    with pytest.raises(RedNoteError):
        store.export(draft["id"], tmp_path / "missing" / "draft.md")
    assert store.undo()["draft"] is None


def test_corrupted_database_has_domain_error(tmp_path):
    (tmp_path / "drafts.sqlite3").write_bytes(b"not sqlite")
    with pytest.raises(RedNoteError):
        DraftStore(tmp_path)


def test_corrupted_document_has_domain_error(tmp_path):
    store = DraftStore(tmp_path)
    draft = store.create("正常")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE drafts SET document = ? WHERE id = ?", ("{bad", draft["id"])
        )
    with pytest.raises(RedNoteError):
        store.get(draft["id"])


def test_future_database_version_is_rejected(tmp_path):
    store = DraftStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RedNoteError):
        DraftStore(tmp_path)


def test_unwritable_home_has_domain_error(tmp_path):
    home = tmp_path / "not-directory"
    home.write_text("existing file", encoding="utf-8")
    with pytest.raises(RedNoteError):
        DraftStore(home)
