import pytest

from research.artifacts import EdgeFamily, Engine
from research.taxonomy import TaxonomyRejection, TaxonomyService


def test_all_approved_families_validate():
    service = TaxonomyService()

    validated = [service.validate(family.value) for family in EdgeFamily]

    assert validated == list(EdgeFamily)


def test_invalid_family_raises():
    service = TaxonomyService()

    with pytest.raises(TaxonomyRejection):
        service.validate("macro_heroics")


def test_get_family_info_returns_metadata():
    service = TaxonomyService()

    info = service.get_family_info(EdgeFamily.TREND_MOMENTUM)

    assert info["primary_engine"] == Engine.ENGINE_A
    assert "Time-series continuation" in info["description"]


def test_suggest_engine_uses_taxonomy_mapping():
    service = TaxonomyService()

    assert service.suggest_engine(EdgeFamily.UNDERREACTION_REVISION) == Engine.ENGINE_B
