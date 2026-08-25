# Groupmate Plugin Page Design QA

- Source visual truth: `docs/qa/2026-08-21/design-reference.png`
- Implementation: `docs/qa/2026-08-21/design-implementation-inspector-dark.png`
- Full-view comparison: `docs/qa/2026-08-21/design-comparison-dark.png`
- Focused table/inspector comparison: `docs/qa/2026-08-21/design-comparison-focus.png`
- Narrow-state evidence: `docs/qa/2026-08-21/design-implementation-narrow.png`
- Supplemental non-text source: `/var/folders/2h/qwsmbj8x7ts6d5hykdppnvvw0000gn/T/codex-clipboard-1e532f57-2d50-4544-a9c7-66133c41159b.png`
- Supplemental implementation evidence: in-app Browser capture at 1280 × 720 CSS px, image-message inspector open, verified in both dark and light themes.
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
- Image quality and assets: supplied Groupmate brand asset is used directly; participant images resolve from the server and fall back to stable initials without broken image elements. Incoming image messages now resolve through an opaque, scoped server reference and render as a contained thumbnail in the list and a larger preview in the inspector. Audio, video, file, QQ expression, forward and card segments retain readable typed fallbacks when no safe preview is available. Passed.
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

### Pass 3 — non-text message extension passed

The supplemental source exposed two rows collapsed to the generic `[非文本消息]` placeholder. The revised implementation preserves ordered message parts, shows a real image thumbnail in the table, expands the image with name and size in the inspector, and renders compact `语音` / `文件` labels for non-previewable parts. The additional content increases only affected row height and does not change the established table, inspector, typography, token or spacing system. No actionable P0/P1/P2 issue remains.

### Pass 4 — responsive topbar regression passed

The supplied dark-theme crop exposed two cascade conflicts at intermediate widths: fixed context-card minimums overlapped the runtime summary, while an obsolete persona row rule pushed the version chip below its card. The context group now owns the available flexible width, both cards shrink safely up to their existing 256 px maximum, the persona row remains horizontal, and compact widths reliably hide summary metrics before they can overflow. Browser geometry checks at 354, 800, 1080, and 1280 CSS px confirmed zero overlap and zero horizontal page overflow.

### Pass 5 — SHADOW observability and neutral theme passed

The runtime center now uses the requested neutral gray/white light theme, with green reserved for SHADOW and healthy-state emphasis. Browser verification covered immediate refresh with preserved filter/search state, image rendering in both table and inspector, a known mention rendered as `@小雨`, candidate response visibility, and two cognition-worker diagnostics. Light and dark themes were inspected at desktop width with no console warnings or errors.

## Interactions and runtime checks

- Tested all/external filters: external filter reduced the fixture from five rows to two.
- Opened a trace and verified the stage inspector.
- Switched between dark and light themes.
- Loaded an image message in the list and opened its full inspector preview.
- Verified typed fallbacks for voice and file message parts.
- Checked the 680 px message-card layout.
- Checked topbar geometry at 354, 800, 1080, and 1280 px; the version chip stays inside the persona card whenever visible.
- Verified immediate refresh preserves the active filter and search text.
- Verified a known `@` mention, SHADOW pre-gate result, candidate reply and cognition diagnostics.
- Browser console: no warnings or errors.

## Follow-up polish

- P3: real QQ avatar sharpness and cache behavior can only be judged against a live NapCat account; the preview intentionally exercised the initials fallback.

final result: passed

---

# Affection Leaderboard Image QA

- Source visual truth: `/Users/minase/.codex/generated_images/01a022e6-b653-7a11-9c6b-0927b93baeb0/exec-5a31b87c-8e3e-404b-8f5d-cd6853dc4259.png`
- Browser-rendered implementation: `/private/tmp/groupmate-affection-preview/large-final2.png`
- Density-normalized implementation: `/private/tmp/groupmate-affection-preview/large-normalized.png`
- Full-view comparison: `/private/tmp/groupmate-affection-preview/comparison.png`
- Focused header/table comparison: `/private/tmp/groupmate-affection-preview/comparison-focus.png`
- Viewport: 1400 × 1200 CSS px; captured content: 1340 × 1145 CSS px.
- Pixel dimensions: source 1339 × 1173; implementation 1340 × 1145 after normalizing the in-app preview surface's half-density capture to the DOM-reported CSS extent.
- State: 228-member public leaderboard, requester at rank 95; supplemental one-member state at 920 × 208 CSS px.

## Full-view comparison evidence

The revised image uses the reference's compact blush canvas, inline title metadata, outlined personal-position strip, five-column dense ranking table, column headers, restrained pink borders, and outlined requester row. The previous large rounded shell, solid-pink content blocks, four-column limit, and empty 1280 × 720 minimum canvas are removed.

## Focused comparison evidence

The focused comparison verifies the title/meta rhythm, the order and emphasis of the requester summary, readable column labels, compact row density, and requester outline. A focused region was required because the 228-member table text is too small to judge reliably from the full image alone.

## Required fidelity surfaces

- Fonts and typography: Chinese system UI stack with antialiasing, tabular numeric scores, stronger title/requester weights, and 11 px dense-table text. Passed.
- Spacing and layout rhythm: 16–18 px canvas margins, 42 px title mark, 48 px requester strip, 21 px large-board rows, and content-sized capture. Passed.
- Colors and visual tokens: near-white blush background, warm white row surfaces, restrained pink rules, dark-plum headings, and distinct positive/negative score colors. Passed.
- Image quality and assets: lossless PNG output, no remote image dependency, and no avatar downscaling or broken-image fallback. The small heart mark is intentionally typographic so it remains sharp at every capture density. Passed.
- Copy and content: group identity, 30-day active count, update time, personal rank, nickname, score, stage, QQ suffix, and column labels remain visible. Passed.

## Comparison history

### Pass 1 — blocked

- P1: one-member output inherited the renderer's 1280 × 720 minimum canvas, leaving most of the image blank.
- P1: the table lacked headers and stopped at four columns, so it did not reproduce the reference's readable information architecture or density.
- P2: the requester strip was a large solid fill and the outer rounded shell weakened the reference's light, precise hierarchy.

Fixes:

- Added content-aware render width and height plus screenshot clipping.
- Added five-column layout for 161–240 members and compact density tiers for smaller boards.
- Added column headers and grouped nickname/QQ suffix into one identity cell.
- Rebuilt the requester strip, row borders, typography, and palette around the supplied reference.

### Pass 2 — passed

Browser geometry confirms a 920 × 208 one-member image and a 1340 × 1145 228-member image. The dense board fits five equal 258.4 px columns with no horizontal overflow, while the small board no longer carries a 720 px minimum height. No actionable P0/P1/P2 mismatch remains.

## Interactions and runtime checks

- Rendered one-member and 228-member states from the production template and presenter context.
- Verified requester highlighting in both the summary strip and ranking row.
- Verified measured canvas size, column count, equal column widths, and content clipping.
- Browser console: no application warnings or errors observed.

final result: passed

### Pass 3 — final six-column capsule reference passed

- Final visual truth: `/var/folders/2h/qwsmbj8x7ts6d5hykdppnvvw0000gn/T/codex-clipboard-7b19c9cb-e254-4236-bc49-1155dddbcf94.png`.
- Replaced the traditional ranked table with the reference's borderless six-column capsule roster.
- Removed repeated column headers, visible rank numbers, and stage cells from each member row; identity, QQ suffix, and score now carry the row.
- Moved the requester summary to a compact top-right rank capsule while retaining a single highlighted member row.
- Restored the reference-scale 1380 px canvas and 240-member page capacity. AstrBot now captures the full page with device scaling instead of a fixed clip rectangle.
- Browser geometry for 228 members: 1380 × 1113 CSS px, six columns, 228 unique rows, requester highlighted once, final column ending at member 228, and no horizontal content overflow.
- Targeted presenter, roster, query, and composition checks: 56 passed.

final result: passed
