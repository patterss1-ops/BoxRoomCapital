from datetime import date

from research.market_data.seed_universe import seed_mvp_universe


def test_seed_mvp_universe_is_idempotent(monkeypatch):
    instruments = {}
    memberships = set()
    contracts = {}
    rolls = {}
    next_id = {"value": 1}

    def _key(symbol, venue, asset_class):
        return (symbol, venue, asset_class)

    def _get_by_symbol(symbol, venue=None, asset_class=None):
        return instruments.get(_key(symbol, venue, asset_class))

    def _create_instrument(instrument):
        instrument = instrument.model_copy(update={"instrument_id": next_id["value"]})
        next_id["value"] += 1
        instruments[_key(instrument.symbol, instrument.venue, instrument.asset_class)] = instrument
        return instrument

    def _update_instrument(instrument_id, **changes):
        for key, item in list(instruments.items()):
            if int(item.instrument_id or 0) == instrument_id:
                updated = item.model_copy(update=changes)
                instruments[key] = updated
                return updated
        raise AssertionError("instrument not found")

    def _add_membership(membership):
        memberships.add((membership.instrument_id, membership.universe, membership.from_date))
        return membership

    def _register_contract(contract):
        contracts[(contract.root_symbol, contract.expiry_date)] = contract
        return contract

    def _add_roll_entry(entry):
        rolls[(entry.root_symbol, entry.roll_date)] = entry
        return entry

    monkeypatch.setattr("research.market_data.seed_universe.get_by_symbol", _get_by_symbol)
    monkeypatch.setattr("research.market_data.seed_universe.create_instrument", _create_instrument)
    monkeypatch.setattr("research.market_data.seed_universe.update_instrument", _update_instrument)
    monkeypatch.setattr("research.market_data.seed_universe.add_membership", _add_membership)
    monkeypatch.setattr("research.market_data.seed_universe.register_contract", _register_contract)
    monkeypatch.setattr("research.market_data.seed_universe.add_roll_entry", _add_roll_entry)

    first = seed_mvp_universe(as_of=date(2026, 3, 9))
    second = seed_mvp_universe(as_of=date(2026, 3, 9))

    assert first == second
    assert len(instruments) == 18 + (18 * 2)
    assert len(contracts) == 18 * 2
    assert len(rolls) == 18
    assert len(memberships) == 18 + (18 * 2)
