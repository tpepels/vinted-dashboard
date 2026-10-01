"""Per-channel connector metadata: which channels have a connector today,
and whether each supports importing (reading listings in) and/or exporting
(pushing inventory out). Used by the dual-write layer in
:mod:`app.connectors.workspace_sync`, and intended as the foundation for a
later connectors status view and the generic import/export framework.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import Channel
from app.workspace_bootstrap import CHANNEL_DISPLAY_NAMES


@dataclass(frozen=True)
class ConnectorInfo:
    channel: str
    display_name: str
    #: Can bring listings into the app (Vinted browser-sync push, eBay
    #: Trading API pull, BIBLIO file import all count as "import").
    supports_import: bool
    #: Can push the app's own inventory state out to the channel (today,
    #: only the BIBLIO FTP export).
    supports_export: bool


CONNECTORS: dict[str, ConnectorInfo] = {
    Channel.VINTED: ConnectorInfo(
        channel=Channel.VINTED,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.VINTED],
        supports_import=True,
        supports_export=False,
    ),
    Channel.EBAY: ConnectorInfo(
        channel=Channel.EBAY,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.EBAY],
        supports_import=True,
        supports_export=False,
    ),
    Channel.BIBLIO: ConnectorInfo(
        channel=Channel.BIBLIO,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.BIBLIO],
        supports_import=True,
        supports_export=True,
    ),
}


def get_connector(channel: str) -> ConnectorInfo | None:
    return CONNECTORS.get(channel)
