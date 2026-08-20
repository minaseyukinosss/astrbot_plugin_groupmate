# Installed SHADOW social judgment rubric

Review one decision point at a time. The visible record contains bounded prior
history, one focus, Attention, target, candidate response/action, Governor outcome,
structured reason codes, and expiry. It intentionally excludes prompts, hidden
reasoning, raw platform identifiers, and internal evidence identifiers.

Choose the primary verdict first:

- `reasonable`: the proposed attention, action/silence, target, intent, modality,
  sensitivity, and expiry are all acceptable in this context.
- `unreasonable`: one or more structured label fields are wrong. Submit a complete
  corrected label; add or replace scene categories only when the suggestion is
  missing or wrong.
- `insufficient`: the visible evidence cannot support a label. Do not infer one.

Historical bootstrap records are regression evidence only. They never count toward
the first-calibration minimum, installed-live scene coverage, or production
readiness. Frozen holdout labels are immutable.
