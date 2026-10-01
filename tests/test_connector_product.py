from app import billing
from app.connectors.base import Capability, get_connector


def test_connector_capabilities_are_explicit():
    vinted = get_connector("vinted")
    biblio = get_connector("biblio")
    assert vinted is not None and vinted.supports(Capability.BROWSER_ASSISTED)
    assert not vinted.supports(Capability.EXPORT_INVENTORY)
    assert biblio is not None and biblio.supports(Capability.SYNC_INVENTORY)


def test_billing_is_disabled_by_default():
    assert billing.BILLING_ENABLED is False
