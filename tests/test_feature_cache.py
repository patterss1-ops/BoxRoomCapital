from tests.research_test_utils import FakeConnection, FakeCursor, make_description

from research.engine_a.feature_cache import FeatureCache


def test_feature_cache_set_upserts_value():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    cache = FeatureCache(connection_factory=lambda: conn, release_factory=lambda connection: None)

    result = cache.set(
        instrument="ES",
        as_of="2026-03-09T00:00:00Z",
        signal_type="trend",
        data_version="v1",
        raw_value=0.42,
        normalized_value=0.3,
        metadata={"lookback": "8/16/32/64"},
    )

    assert result["instrument"] == "ES"
    assert any("INSERT INTO research.feature_cache" in sql for sql, _ in cursor.executed)
    assert conn.committed is True


def test_feature_cache_get_returns_cached_row():
    cursor = FakeCursor(
        fetchone_results=[
            ("ES", "2026-03-09", "trend", "v1", 0.42, 0.3, {"lookback": "8/16"}, "2026-03-09T00:00:00Z")
        ],
        descriptions=[
            make_description(
                "instrument",
                "as_of",
                "signal_type",
                "data_version",
                "raw_value",
                "normalized_value",
                "metadata",
                "computed_at",
            )
        ],
    )
    conn = FakeConnection(cursor)
    cache = FeatureCache(connection_factory=lambda: conn, release_factory=lambda connection: None)

    result = cache.get("ES", "2026-03-09T12:00:00Z", "trend", "v1")

    assert result["normalized_value"] == 0.3
    assert result["metadata"] == {"lookback": "8/16"}


def test_invalidate_stale_versions_deletes_old_rows():
    cursor = FakeCursor(rowcount=2)
    conn = FakeConnection(cursor)
    cache = FeatureCache(connection_factory=lambda: conn, release_factory=lambda connection: None)

    deleted = cache.invalidate_stale_versions(
        instrument="ES",
        as_of="2026-03-09T00:00:00Z",
        signal_type="trend",
        current_data_version="v2",
    )

    assert deleted == 2
    assert any("DELETE FROM research.feature_cache" in sql for sql, _ in cursor.executed)


def test_get_or_compute_avoids_recomputation_on_cache_hit():
    cursor = FakeCursor(
        fetchone_results=[
            ("ES", "2026-03-09", "trend", "v1", 0.42, 0.3, {}, "2026-03-09T00:00:00Z")
        ],
        descriptions=[
            make_description(
                "instrument",
                "as_of",
                "signal_type",
                "data_version",
                "raw_value",
                "normalized_value",
                "metadata",
                "computed_at",
            )
        ],
    )
    conn = FakeConnection(cursor)
    cache = FeatureCache(connection_factory=lambda: conn, release_factory=lambda connection: None)
    compute_calls = {"count": 0}

    result = cache.get_or_compute(
        instrument="ES",
        as_of="2026-03-09T00:00:00Z",
        signal_type="trend",
        data_version="v1",
        compute_fn=lambda: compute_calls.__setitem__("count", compute_calls["count"] + 1) or {
            "raw_value": 1.0,
            "normalized_value": 1.0,
        },
    )

    assert result["normalized_value"] == 0.3
    assert compute_calls["count"] == 0
