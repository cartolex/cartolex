# SPDX-License-Identifier: MIT
"""The settings' tools: keys kept on this computer, stop words, prompts, backup, restore, reset."""

from __future__ import annotations

import io
import json
import stat
import zipfile

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.project import Project
from cartolex.project.models import Slot


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    root = tmp_path / "p"
    Project.init(
        root,
        name="Small map",
        domain_title="Ocean physics",
        corpus_languages=("en", "fr"),
        slots=(Slot(id="collected", kind="collection"),),
    ).close()
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "data"))
    c = Client(app)
    c.root, c.data = root, tmp_path / "data"
    yield c
    app.state.cartolex.shutdown()


def test_a_key_is_kept_on_this_computer_never_in_the_project(client):
    assert client.get("/api/machine").json()["keys"]["mistral"]["set"] is False
    saved = client.put("/api/machine/keys", json={"service": "mistral", "key": "sk-test-12345678"})
    status = saved.json()["keys"]["mistral"]
    assert status == {
        "set": True,
        "source": "saved",
        "env_var": "MISTRAL_API_KEY",
        "saved": True,
        "ends": "5678",
    }
    path = client.data / "keys.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "sk-test-12345678" not in json.dumps(saved.json())
    assert not any(
        "sk-test" in p.read_text(errors="ignore") for p in client.root.rglob("*") if p.is_file()
    )
    assert client.get("/api/app/manifest").json()["capabilities"]["ai_api"] is True
    client.put("/api/machine/keys", json={"service": "mistral", "key": None})
    assert client.get("/api/app/manifest").json()["capabilities"]["ai_api"] is False


def test_stop_words_and_a_prompt_of_the_project(client):
    got = client.get("/api/settings/stopwords")
    both = client.put(
        "/api/settings/stopwords",
        json={"add": {"en": ["model"]}, "remove": {"en": ["Model"]}},
        headers={"If-Match": etag(got)},
    )
    assert both.status_code == 422 and both.json()["error"]["code"] == "stopword_both"
    put = client.put(
        "/api/settings/stopwords",
        json={"add": {"en": ["Model ", "model", "data"]}},
        headers={"If-Match": etag(got)},
    )
    assert put.json()["add"] == {"en": ["data", "model"]}
    prompts = client.get("/api/settings/prompts").json()["items"]
    prompt = prompts[0]
    assert prompt["own"] is None and "domain_title" in prompt["placeholders"]
    bad = client.put(
        f"/api/settings/prompts/{prompt['name']}",
        json={"text": "Sort {terms} {oops}"},
        headers={"If-Match": prompt["version"]},
    )
    assert bad.status_code == 422 and bad.json()["error"]["params"]["unknown"] == ["oops", "terms"]
    own = client.put(
        f"/api/settings/prompts/{prompt['name']}",
        json={"text": "Field: {domain_title}"},
        headers={"If-Match": prompt["version"]},
    )
    assert own.json()["own"] == "Field: {domain_title}"
    back = client.put(
        f"/api/settings/prompts/{prompt['name']}",
        json={"text": None},
        headers={"If-Match": etag(own)},
    )
    assert back.json()["own"] is None
    assert list((client.root / "decisions" / "history" / "prompts").iterdir())


def test_backup_restore_and_reset(client):
    got = client.get("/api/settings/stopwords")
    first = client.put(
        "/api/settings/stopwords", json={"add": {"en": ["alpha"]}}, headers={"If-Match": etag(got)}
    )
    backup = client.get("/api/settings/backup")
    names = zipfile.ZipFile(io.BytesIO(backup.content)).namelist()
    assert (
        "project.json" in names and "decisions/stopwords.json" in names and "backup.json" in names
    )
    client.put(
        "/api/settings/stopwords", json={"add": {"en": ["beta"]}}, headers={"If-Match": etag(first)}
    )
    restored = client.post(
        "/api/settings/restore", files={"file": ("b.zip", backup.content, "application/zip")}
    )
    assert restored.status_code == 200 and "decisions/stopwords.json" in restored.json()["restored"]
    assert client.get("/api/settings/stopwords").json()["add"] == {"en": ["alpha"]}
    wrong = client.post(
        "/api/settings/restore", files={"file": ("x.zip", b"not a zip", "application/zip")}
    )
    assert wrong.json()["error"]["code"] == "not_a_backup"
    (client.root / "derived" / "keywords.build").mkdir(parents=True)
    assert client.post("/api/settings/reset", json={"what": "built"}).json()["removed"] == 1
    assert not any((client.root / "derived").iterdir())
    assert (client.root / "decisions" / "stopwords.json").exists()
