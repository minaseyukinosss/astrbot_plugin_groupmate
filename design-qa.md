# Groupmate runtime UI design QA

- Reference: user-provided neutral gray/white application screenshot (style direction only)
- Checked page: `/fake/settings/index.html#/runtime`
- Checked themes: light and dark
- Checked viewport: desktop in-app browser

## Verified

- Neutral gray/white canvas, sidebar and selected navigation in light theme
- Green reserved for SHADOW and healthy-state emphasis
- Header context cards and runtime summary do not overlap at the checked width
- Immediate refresh preserves the active filter and search query
- Image preview renders in the message row and detail panel
- Known `@` mention renders as `@小雨` with an avatar placeholder/reference
- SHADOW detail exposes the pre-gate decision, candidate response and cognition diagnostics
- No browser console warnings or errors

final result: passed
