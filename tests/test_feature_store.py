"""Tests for M-002 feature store."""

from __future__ import annotations

from intelligence.feature_store import FeatureRecord, FeatureStore


def _rec(
    entity_id: str = "AAPL",
    event_ts: str = "2026-03-03T12:00:00+00:00",
    feature_set: str = "alpha_v1",
    feature_version: int = 1,
    features: dict[str, float] | None = None,
):
    return FeatureRecord(
        entity_id=entity_id,
        event_ts=event_ts,
        feature_set=feature_set,
        feature_version=feature_version,
        features=features or {"mom_5d": 0.12, "vol_20d": 0.28},
    )


def test_save_and_get_round_trip():
    store = FeatureStore()
    r = _rec()
    rid = store.save(r)
    got = store.get(rid)
    assert got is not None
    assert got.entity_id == "AAPL"
    assert got.feature_set == "alpha_v1"
    assert got.features["mom_5d"] == 0.12
    store.close()


def test_save_batch_and_count():
    store = FeatureStore()
    ids = store.save_batch(
        [
            _rec(entity_id="AAPL", event_ts="2026-03-01T12:00:00+00:00"),
            _rec(entity_id="AAPL", event_ts="2026-03-02T12:00:00+00:00"),
            _rec(entity_id="MSFT", event_ts="2026-03-02T12:00:00+00:00"),
        ]
    )
    assert len(ids) == 3
    assert store.count() == 3
    assert store.count(entity_id="AAPL") == 2
    store.close()


def test_query_filters_and_pagination():
    store = FeatureStore()
    for i in range(8):
        store.save(
            _rec(
                entity_id="AAPL",
                event_ts=f"2026-03-03T12:{i:02d}:00+00:00",
                feature_version=1 if i < 4 else 2,
            )
        )
    v2 = store.query(entity_id="AAPL", feature_version=2, limit=10)
    assert len(v2) == 4
    page1 = store.query(entity_id="AAPL", limit=3, offset=0)
    page2 = store.query(entity_id="AAPL", limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    assert {r.record_id for r in page1}.isdisjoint({r.record_id for r in page2})
    store.close()


def test_get_latest_with_version_filter():
    store = FeatureStore()
    store.save(_rec(event_ts="2026-03-03T10:00:00+00:00", feature_version=1))
    store.save(_rec(event_ts="2026-03-03T11:00:00+00:00", feature_version=2))
    store.save(_rec(event_ts="2026-03-03T12:00:00+00:00", feature_version=1))
    latest_any = store.get_latest("AAPL", "alpha_v1")
    latest_v1 = store.get_latest("AAPL", "alpha_v1", feature_version=1)
    assert latest_any is not None and latest_any.event_ts == "2026-03-03T12:00:00+00:00"
    assert latest_v1 is not None and latest_v1.event_ts == "2026-03-03T12:00:00+00:00"
    store.close()


def test_point_in_time_retrieval():
    store = FeatureStore()
    store.save(_rec(event_ts="2026-03-03T09:00:00+00:00", features={"x": 1.0}))
    store.save(_rec(event_ts="2026-03-03T10:00:00+00:00", features={"x": 2.0}))
    store.save(_rec(event_ts="2026-03-03T11:00:00+00:00", features={"x": 3.0}))
    pit = store.get_point_in_time("AAPL", "alpha_v1", "2026-03-03T10:30:00+00:00")
    assert pit is not None
    assert pit.event_ts == "2026-03-03T10:00:00+00:00"
    assert pit.features["x"] == 2.0
    none_case = store.get_point_in_time("AAPL", "alpha_v1", "2026-03-03T08:00:00+00:00")
    assert none_case is None
    store.close()


def test_training_set_ordered_ascending():
    store = FeatureStore()
    store.save(_rec(event_ts="2026-03-03T11:00:00+00:00", features={"x": 3.0}))
    store.save(_rec(event_ts="2026-03-03T09:00:00+00:00", features={"x": 1.0}))
    store.save(_rec(event_ts="2026-03-03T10:00:00+00:00", features={"x": 2.0}))
    rows = store.get_training_set(
        entity_id="AAPL",
        feature_set="alpha_v1",
        start_ts="2026-03-03T09:00:00+00:00",
        end_ts="2026-03-03T11:00:00+00:00",
    )
    assert [r.event_ts for r in rows] == [
        "2026-03-03T09:00:00+00:00",
        "2026-03-03T10:00:00+00:00",
        "2026-03-03T11:00:00+00:00",
    ]
    store.close()


def test_delete_before_and_count():
    store = FeatureStore()
    store.save(_rec(event_ts="2026-03-01T10:00:00+00:00"))
    store.save(_rec(event_ts="2026-03-02T10:00:00+00:00"))
    store.save(_rec(event_ts="2026-03-03T10:00:00+00:00"))
    deleted = store.delete_before("2026-03-03T00:00:00+00:00")
    assert deleted == 2
    assert store.count() == 1
    store.close()
