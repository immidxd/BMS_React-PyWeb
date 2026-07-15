from backend.services import prom_service


def _configured(monkeypatch):
    monkeypatch.setattr(
        prom_service,
        "_load_config",
        lambda _db: {"api_token": "test-token", "token_expires_at": None},
    )


def test_official_success_is_authoritative(monkeypatch):
    _configured(monkeypatch)
    calls = []

    def fake_get(_token, path, params=None):
        calls.append((path, params))
        return {"id": 42, "status": "SUCCESS", "imported": 2}

    monkeypatch.setattr(prom_service, "_api_get", fake_get)

    result = prom_service.prom_import_progress(object(), import_id=42, skus=["A", "B"])

    assert result["done"] is True
    assert result["status"] == "SUCCESS"
    assert result["source"] == "import_status"
    assert result["details"]["imported"] == 2
    assert calls == [("/products/import/status/42", None)]


def test_official_pending_does_not_use_presence_fallback(monkeypatch):
    _configured(monkeypatch)
    calls = []

    def fake_get(_token, path, params=None):
        calls.append((path, params))
        return {"id": 43, "status": "PENDING"}

    monkeypatch.setattr(prom_service, "_api_get", fake_get)

    result = prom_service.prom_import_progress(object(), import_id=43, skus=["A"])

    assert result["done"] is False
    assert result["status"] == "PENDING"
    assert result["source"] == "import_status"
    assert calls == [("/products/import/status/43", None)]


def test_sku_fallback_deduplicates_and_confirms_all(monkeypatch):
    _configured(monkeypatch)

    def fake_get(_token, path, params=None):
        assert path == "/products/list"
        return {"products": [
            {"id": 12, "sku": "A", "presence": "available"},
            {"id": 11, "sku": "B", "presence": "not_available"},
        ]}

    monkeypatch.setattr(prom_service, "_api_get", fake_get)

    result = prom_service.prom_import_progress(object(), skus=["A", "A", " B "])

    assert result["done"] is True
    assert result["status"] == "SUCCESS"
    assert result["expected"] == 2
    assert result["found"] == 2
    assert result["presence"] == {"available": 1, "not_available": 1}


def test_missing_sku_remains_retryable(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(
        prom_service,
        "_api_get",
        lambda _token, _path, _params=None: {
            "products": [{"id": 12, "sku": "A", "presence": "available"}]
        },
    )

    result = prom_service.prom_import_progress(object(), skus=["A", "B"])

    assert result["done"] is False
    assert result["retryable"] is True
    assert result["found"] == 1
    assert result["missing_skus"] == ["B"]


def test_partial_is_terminal_but_not_success(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(
        prom_service,
        "_api_get",
        lambda _token, _path, _params=None: {
            "id": 77, "status": "PARTIAL", "imported": 8, "failed": 2
        },
    )

    result = prom_service.prom_import_progress(object(), import_id=77, skus=["A", "B"])

    assert result["done"] is True
    assert result["ok"] is False
    assert result["status"] == "PARTIAL"
    assert result["details"] == {"imported": 8, "failed": 2}


def test_batch_response_marks_only_visible_skus_for_fallback(monkeypatch):
    _configured(monkeypatch)
    feed = (
        '<price><items>'
        '<item id="A" available="true"></item>'
        '<item id="B" available="false"></item>'
        '</items></price>'
    )
    monkeypatch.setattr(
        prom_service, "build_batch_feed",
        lambda _db, _ids, available=True: (feed, ["A", "B"], []),
    )
    monkeypatch.setattr(prom_service, "_queue_draft", lambda _db, _sku: None)
    monkeypatch.setattr(
        prom_service, "_submit_feed",
        lambda _db, _token, _feed, _skus, _label: {
            "ok": True, "queued": True, "import_id": None, "note_tail": ""
        },
    )

    result = prom_service.export_products_batch(object(), [1])

    assert result["ok"] is True
    assert result["skus"] == ["A", "B"]
    assert result["visible_skus"] == ["A"]
