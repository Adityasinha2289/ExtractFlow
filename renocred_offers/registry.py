"""Adapter registry - maps a source config's ``adapter`` key to its class."""

from typing import Any

import yaml

from renocred_offers.adapters.aggregator import AggregatorTableAdapter, GrabOnAdapter
from renocred_offers.adapters.axis_edge import AxisEdgeAdapter
from renocred_offers.adapters.gyftr import GyftrAdapter
from renocred_offers.adapters.network import PricelessAdapter, VisaWelcomeAdapter
from renocred_offers.adapters.sbicard import SbiCardFestiveAdapter, SbiCardOffersAdapter
from renocred_offers.adapters.smartbuy import SmartBuyTermsAdapter

ADAPTERS = {
    "sbicard_offers": SbiCardOffersAdapter,
    "sbicard_festive": SbiCardFestiveAdapter,
    "gyftr": GyftrAdapter,
    "smartbuy_terms": SmartBuyTermsAdapter,
    "axis_edge": AxisEdgeAdapter,
    "visa_welcome": VisaWelcomeAdapter,
    "priceless": PricelessAdapter,
    "aggregator_tables": AggregatorTableAdapter,
    "grabon": GrabOnAdapter,
}


def load_sources(path: str) -> dict[str, Any]:
    """Load and lightly validate the source registry YAML."""
    with open(path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    for source in config.get("sources") or []:
        adapter = source.get("adapter")
        if adapter not in ADAPTERS:
            raise ValueError(
                f"source {source.get('key')!r} names unknown adapter {adapter!r}; "
                f"known: {sorted(ADAPTERS)}"
            )
    return config


def build_adapter(source: dict[str, Any], merchants, cards):
    return ADAPTERS[source["adapter"]](source, merchants, cards)
