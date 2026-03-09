"""Approved edge-family taxonomy for research hypotheses."""

from __future__ import annotations

from research.artifacts import EdgeFamily, Engine


class TaxonomyRejection(Exception):
    """Raised when a hypothesis does not map to the approved taxonomy."""


class TaxonomyService:
    """Enforce edge family classification on all hypotheses."""

    APPROVED_FAMILIES = list(EdgeFamily)

    FAMILY_DESCRIPTIONS = {
        EdgeFamily.UNDERREACTION_REVISION: {
            "description": "Post-earnings drift, analyst revision, slow information diffusion",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["equities", "sector_etfs"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.CARRY_RISK_TRANSFER: {
            "description": "Interest rate differential, term premium, insurance premium",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "fx", "crypto_basis"],
            "primary_engine": Engine.ENGINE_A,
        },
        EdgeFamily.TREND_MOMENTUM: {
            "description": "Time-series continuation, cross-sectional momentum",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "equities", "etfs"],
            "primary_engine": Engine.ENGINE_A,
        },
        EdgeFamily.FLOW_POSITIONING: {
            "description": "Hedging pressure, forced selling, index rebalancing",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["equities", "futures"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.RELATIVE_VALUE: {
            "description": "Law-of-one-price violations, temporary divergences",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["pairs", "etfs", "futures_spreads"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.CONVEXITY_INSURANCE: {
            "description": "Variance risk premium, skew premium, event-specific vol",
            "typical_horizon": "days to expiry",
            "typical_instruments": ["options"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.REGIME_DISLOCATION: {
            "description": "Structural breaks, liquidity regime shifts, policy regime changes",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "fx", "rates"],
            "primary_engine": Engine.ENGINE_A,
        },
    }

    def validate(self, edge_family: str) -> EdgeFamily:
        try:
            return EdgeFamily(edge_family)
        except ValueError as exc:
            raise TaxonomyRejection(
                f"Edge family '{edge_family}' not in approved taxonomy. "
                f"Approved: {[family.value for family in self.APPROVED_FAMILIES]}"
            ) from exc

    def get_family_info(self, family: EdgeFamily) -> dict:
        return self.FAMILY_DESCRIPTIONS[family]

    def suggest_engine(self, family: EdgeFamily) -> Engine:
        return self.FAMILY_DESCRIPTIONS[family]["primary_engine"]
