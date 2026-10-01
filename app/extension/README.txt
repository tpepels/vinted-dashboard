Reseller Dashboard Chrome Bridge

This is the Chrome Web Store source variant. Build it with
scripts/build_extension.py.

Single purpose: synchronize the reseller data needed by a paired workspace
from the user's signed-in Vinted account.

The extension stores only the revocable dashboard bridge token, pairing/sync
metadata and remembered Vinted origin in chrome.storage.local. It does not
export Vinted passwords or raw cookie values. Unrelated Vinted notification
text is discarded before upload.

The separate app/legacy_extension directory preserves the self-hosted personal
variant, including the older opt-in market-research workflow.
