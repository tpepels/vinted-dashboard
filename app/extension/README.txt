Reseller Dashboard Chrome Bridge

This is the Chrome Web Store source variant. Build it with scripts/build_extension.py.
It pairs with a reseller workspace using a short-lived code and stores only the
revocable dashboard bridge token in chrome.storage.local. It does not export
Vinted password or cookie values.

The separate app/legacy_extension directory preserves the self-hosted personal
variant, including the older opt-in market-research workflow.
