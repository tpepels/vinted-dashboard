# Dashboard UI rules

Keep this file as the default review checklist for new inventory and marketplace features.

## Semantic color

Color communicates **state**, not marketplace brand.

| Role | Token | Meaning |
|---|---|---|
| Primary/teal | `--accent`, `--accent-strong`, `--soft` | Action, selection, current view, ready-to-act |
| Green | `--state-success*` | Independently confirmed / completed successfully |
| Amber | `--state-warning*` | Review required, difference detected, potentially incomplete |
| Red | `--state-danger*` | Failed, destructive action, or blocking error |
| Blue | `--state-info*` | Waiting, working, importing or an informative notice |
| Grey | `--state-neutral*` | Unchecked, not connected, unavailable, descriptive metadata |

Use a visible text label alongside color. A successful FTP transfer is **not**
proof that BIBLIO published the listing or its photos. A completed local
operation without remote verification is **neutral**, never green.
A ready-to-publish cross-list candidate is teal, not a completed sale.

Do not add one-off channel-colored badges, colored summary panels, or use
red/green alone to distinguish states. Keep the existing dark diagnostics log
as a separate technical surface.

## Table behavior

- The outer application never grows horizontally to accommodate a table.
  The table scroll container owns overflow.
- All ordinary read-only tables get sortable, keyboard-focusable headers.
  On fine-pointer desktops, column separators can be dragged or changed with
  Left/Right arrows (Shift for larger increments); double-click resets.
- Remember widths per view and header structure, not globally. On schema
  changes, discard old saved widths.
- Editable scanning/intake tables should preserve predictable field widths
  rather than have resize controls.
- Size tables according to their number of fields instead of compressing 12
  columns into a narrow fixed minimum. Horizontal scrolling belongs to the
  table region, never the whole page.
- When a table's own container is 860px wide or less, Inventory, Listings,
  Sales and the four Vinted analytics tables become labelled row cards
  with two, three or four fact columns depending on the available width. Show every value and preserve the original buttons and checkboxes.
  Keep compact mobile sort controls, plus Select all for inventory. Existing
  listing-specific sorting remains the authority for listings.
- Keep dense editable stock scanning and reconciliation matrices as tables
  with local horizontal scrolling. Do not convert input fields into cards.
- On a wider fine-pointer view, keep draggable/keyboard column resizing;
  saved desktop widths must not force card layouts to overflow.
- Re-run browser-level checks for widths 360, 390, 768, 1024 and 1440
  using representative table rows, checking actions, selection, sorting,
  scroll containment and screenshots.

## Viewports to check

- **1440 px desktop:** balanced sidebar and content, no excess fixed widths.
- **1024 px laptop:** table contained inside main; forms fall back to two columns.
- **768 px tablet:** top navigation, one-column cards, horizontal data tables.
- **390/360 px phone:** no body-level horizontal overflow; cards/forms stack;
  buttons remain operable; table scroll is local to the table.

## Usability acceptance

For every new screen confirm: (1) the primary action is obvious,
(2) statuses are labeled and semantically colored, (3) a marketplace write
is distinguishable from a read-only check, (4) errors include a next action,
(5) technical detail stays collapsed until requested, (6) narrow viewports
keep every control reachable, and (7) focus indicators and keyboard access
are preserved.

The automated CSS/table smoke test checks invariants; screenshots and real
browser/device interactions remain a separate acceptance step.
