# Groupmate 运行中心设计核验

## 比较基准

- Source visual truth: `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/design-qa-source.png`
- Implementation (base state): `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/design-qa-implementation-final.png`
- Implementation (SHADOW inspector open): `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/design-qa-inspector-final.png`
- Combined comparison evidence: `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/design-qa-comparison.png`
- Viewport: 1536 × 1024 CSS px
- Source pixels: 1536 × 1024
- Implementation pixels: 1536 × 1024
- Device scale factor: 1; no density normalization was required.
- State: dark theme, runtime center, SHADOW active, group and persona selected; inspector comparison uses the first visible SHADOW decision.

## Full-view comparison

The combined comparison confirms the reference hierarchy is preserved: fixed left navigation, compact group/persona/runtime context across the top, a dense event table as the primary surface, four compact status panels below, and a right-side decision/evidence inspector. The implementation intentionally omits reference modules whose projections do not exist yet, rather than presenting invented uptime, member count, load, or audit actions.

## Focused-region comparison

The lower half of `design-qa-comparison.png` places the reference and implementation evidence inspectors together at 1:1 source pixels. Both use the same hierarchy: event identity, context, motive/attention, decision and action, governance facts, and evidence. The implementation uses privacy-safe projection labels and refs instead of the reference's illustrative raw participant information.

## Required fidelity surfaces

- Fonts and typography: system Chinese UI stack, compact 11–15 px operational hierarchy, tabular numeric values, and dense table copy are consistent with the reference. No clipping was found at the target viewport.
- Spacing and layout rhythm: region proportions, 1 px separators, compact radii, event-row density, and panel gaps now match the source's operations-console rhythm. Eight rows appear before “加载更多”, keeping the lower status panels visible.
- Colors and tokens: dark charcoal surfaces, restrained blue information chips, green SHADOW/healthy states, amber pending states, and red alerts align with the source. The existing light token set remains available through the theme toggle.
- Image quality and assets: the product mark is a generated raster asset sized for the 40 px brand slot. All interface icons use local Tabler icon assets; no emoji, text glyph, inline SVG, placeholder, or broken image remains.
- Copy and content: labels are adapted to Groupmate's actual projections. “SHADOW 观察已开启” explicitly states that cognition still runs while delivery is disabled.

## Comparison history

1. Initial capture: `design-qa-implementation-1.png`
   - [P1] Local preview did not map nested asset URLs, so brand and icons rendered as broken images.
   - Fix: mapped `/fake/settings/assets/` to production assets and verified zero broken images.
2. Second capture: `design-qa-implementation-2.png`
   - [P2] Event rows were substantially taller than the reference, pushing all status panels below the fold.
   - [P1] Opening a decision after the page scrolled could place the inspector header above the viewport.
   - Fixes: restored table-cell layout, limited the first view to eight rows with a working “加载更多” control, and made the inspector sticky with its own viewport scroll.
3. Final captures: `design-qa-implementation-final.png` and `design-qa-inspector-final.png`
   - The earlier P1/P2 findings are no longer present. The final browser capture has zero broken images and no warning/error console entries.

## Interaction evidence

- “参与判断” tab filters the table to 3 SHADOW decisions.
- “加载更多” expands the event list from 8 to 13 records.
- Selecting a SHADOW row opens context, attention, candidate response/action, constraints, and evidence.
- Theme toggle changes the document from dark to light and back.
- Browser console warnings/errors checked: none.

## Findings

No actionable P0, P1, or P2 differences remain. The narrower first-stage module set is an intentional product constraint: only information supplied by current Groupmate projections is shown.

## Follow-up polish

- [P3] When relationship and trend projections become available, the lower panels can add the source reference's richer member/load trend visuals without changing the shell.

final result: passed
