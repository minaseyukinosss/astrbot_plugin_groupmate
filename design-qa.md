# Groupmate Plugin Page Design QA

- Source visual truth: `docs/qa/2026-08-21/design-reference.png`
- Implementation: `docs/qa/2026-08-21/design-implementation-inspector-dark.png`
- Full-view comparison: `docs/qa/2026-08-21/design-comparison-dark.png`
- Focused table/inspector comparison: `docs/qa/2026-08-21/design-comparison-focus.png`
- Narrow-state evidence: `docs/qa/2026-08-21/design-implementation-narrow.png`
- Viewport: 1536 × 1024 CSS px for the reference-aligned dark state; 680 × 900 CSS px for the narrow state.
- Pixel dimensions: source and desktop implementation are both 1536 × 1024 at device scale factor 1. The combined comparison is 3072 × 1064. No density normalization was required.
- State: SHADOW enabled, five recent messages, first message inspector open, dark theme.

## Full-view comparison evidence

The implementation retains the reference's narrow operations sidebar, compact top context cards, dense central event table, semantic status color, and right-side evidence panel. It intentionally reduces the reference's many product domains to one runtime surface because the approved Groupmate scope is message understanding and participation, not a general AstrBot administration suite.

## Focused comparison evidence

The focused comparison shows that the message table and inspector use the same compact rhythm as the reference while replacing generic internal events with one row per group message. The right panel follows the same evidence hierarchy but exposes sender, routing, stage timeline, understanding, decision, and delivery in plain language. A separate focused crop was used because these labels are too small to judge reliably from the full-page comparison alone.

## Required fidelity surfaces

- Fonts and typography: system Chinese UI stack, compact 11–14 px operational text, clear weight hierarchy, two-line truncation for long messages, and tabular timestamps. Passed.
- Spacing and layout rhythm: sidebar and inspector proportions align with the reference; context cards, table rows, borders, and 6–14 px spacing create the same dense control-center rhythm. Passed.
- Colors and visual tokens: near-black green-neutral surfaces, low-contrast borders, restrained green/blue/amber/red statuses, and matching light-theme semantic surfaces. Passed.
- Image quality and assets: supplied Groupmate brand asset is used directly; participant images resolve from the server and fall back to stable initials without broken image elements. No mock asset replaces required product imagery. Passed.
- Copy and content: internal values are translated to clear Chinese; OFF, SHADOW, external handoff, silence, send success, and unknown delivery are unambiguous. Passed.

## Comparison history

### Pass 1 — blocked

- P1: opening the inspector occupied the top grid column and squeezed the context cards and runtime summary.
- P2: the top context cards exceeded the fixed header height and clipped their labels.
- P2: the inspector displayed the raw understanding state `READY`.

Fixes:

- Made the topbar span both main and inspector columns; the inspector now starts below it.
- Constrained context cards to a 64 px two-row layout.
- Translated understanding states to plain Chinese.

### Pass 2 — passed

Post-fix evidence in the desktop and focused comparison shows a stable full-width topbar, unclipped group/persona cards, a reference-proportioned inspector, and readable Chinese state labels. No actionable P0/P1/P2 differences remain. The smaller navigation and reduced dashboard module count are intentional product-scope decisions.

## Interactions and runtime checks

- Tested all/external filters: external filter reduced the fixture from five rows to two.
- Opened a trace and verified the stage inspector.
- Switched between dark and light themes.
- Checked the 680 px message-card layout.
- Browser console: no warnings or errors.

## Follow-up polish

- P3: real QQ avatar sharpness and cache behavior can only be judged against a live NapCat account; the preview intentionally exercised the initials fallback.

final result: passed
