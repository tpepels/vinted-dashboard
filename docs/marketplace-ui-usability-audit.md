# Marketplace UI usability comparison

## Scope

The comparison covers **Inventory → Marketplaces**, specifically the
per-item marketplace status and editing controls. This is not an evaluation
of the entire application or live seller account.

- **Before:** main commit `874029bb0e9c81ad61af465ff3169ad32606c13a`
- **After:** PR #96, `ux/marketplaces-evidence-first`
- **Fixture:** one physical book with two available copies; WooCommerce,
  Shopify and Wix have mismatched remote quantity (5 vs local 2) and price
  (€8.50 vs €11.25); BIBLIO has a remote comparison discrepancy.
- **Method:** execute the actual before/after `renderItemMarketplacePanel`
  functions in isolated Node VM contexts using identical inputs; count
  initially visible buttons, all existing buttons and text paragraphs while
  respecting whether ancestor `details` elements are open. Do not count
  closed disclosure buttons as initially visible.
- **Browser:** render generated HTML snapshots with their corresponding
  before/after production CSS in headless Chromium. Measure horizontal page
  overflow at two viewport sizes.

## Results

| Measure | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| Immediately visible buttons | 16 | 2 | **87.5% fewer** competing first-screen buttons |
| Available action buttons in the DOM | 16 | 16 | **No action paths removed** |
| Visible paragraph elements | 20 | 7 | **65% fewer** first-screen paragraphs |
| Native disclosure sections | 4 | 12 | Uncommon actions and technical metadata remain discoverable |
| Explicit stock comparisons (dashboard / remote) | Not provided as side-by-side facts | 3 | Remote quantity is labeled unknown if not checked |
| Explicit price comparisons (dashboard / remote) | Not provided as side-by-side facts | 3 | Clearly distinguishes prices and stock |
| Horizontal overflow, width 540 px | Not asserted | None | Chromium `scrollWidth=540`, `innerWidth=540` |
| Horizontal overflow, width 1280 px | Not asserted | None | Chromium `scrollWidth=1280`, `innerWidth=1280` |

These are reproducible *structural usability proxies*, not measured human
task completion times or user preference scores. The screenshots are
**rendered fixtures**, not live authenticated marketplace pages. Checks at
540 and 1280 px do not guarantee every intermediate viewport or browser.

## Interaction and safety safeguards

- Discrepancies remain readable without expanding panels.
- Stock and price edits require an explicit Manage disclosure; their existing
  preflight, server validation, explicit confirmation and remote readback
  paths remain unchanged.
- Listing IDs are available in a technical details disclosure, rather than
  competing with decision-critical values.
- Native `details/summary` preserves keyboard activation and exposes open
  state semantically. Focus-visible styling is supplied.
- The API returns `remote_stock_quantity` only if an integer observation
  has been recorded; it never substitutes the local quantity for an
  unverified remote result.

## Reproduce

```sh
node scripts/test_marketplace_ui_evidence.js
node scripts/test_marketplace_visual_layout.js
```

The evidence script requires Git history that includes the baseline commit.
Chromium is required for the screenshot and viewport test.

GitHub Actions run:
https://github.com/tpepels/vinted-dashboard/actions/runs/37911510290

[Download before/after screenshots and machine-readable results](https://github.com/tpepels/vinted-dashboard/actions/runs/37911510290/artifacts/11606747323)

The CI artifact includes `before-540.png`, `after-540.png`,
`before-1280.png`, `after-1280.png`, both HTML fixtures, measured
`metrics.json`, and `viewport-results.json`. The artifact expires on
23 October 2026.
