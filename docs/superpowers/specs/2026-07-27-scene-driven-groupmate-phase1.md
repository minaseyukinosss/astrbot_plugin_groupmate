# Scene-Driven Groupmate Phase 1 Design

## Goal

Match the target QQ bot's interaction behavior by mapping observable user behavior to explicit interaction scenes. Aggregate ratios from the export are evaluation checks, not runtime probabilities.

## Evidence

- Natural replies are short: median 21 characters and p90 49 characters.
- 46.6% of strict dialogue replies use a visible quote.
- 39.0% of strict dialogue replies contain an inline image.
- 86.4% of dialogue runs contain one dialogue message.
- Only 3.0% of dialogue runs address multiple users.
- Memory-like cues occur in at most 11.9% of dialogue replies and include false positives.

## Decisions

1. Classify each turn into a scene from deterministic interaction evidence: direct address, reply to the bot, active continuation, social response, ambient contribution, or task request.
2. Scene policy determines quote behavior, scheduling priority, reply mode, and later reaction eligibility. It never samples a target ratio.
3. Replace the single deferred message with a FIFO for hard turns. Keep one coalescing slot for soft traffic.
4. Store active continuation grants per sender. Do not add persistent conversation lanes because multi-user dialogue runs are rare.
5. Deliver the first segment as an actual OneBot reply when the scene policy requires a quote.
6. Measure conditional metrics by scene in replay evaluation. Overall ratios remain regression signals only.

## Scene Policy

| Scene | User evidence | Scheduling | Quote rule | Reply behavior |
|---|---|---|---|---|
| `DIRECT_ADDRESS` | native @ or leading alias | hard FIFO | quote anchor | short direct reply |
| `REPLY_TO_BOT` | platform reply references the bot | hard FIFO | quote anchor | short continuation |
| `ACTIVE_CONTINUATION` | same sender has a valid grant | hard FIFO | quote if interleaved | preserve sender context |
| `SOCIAL_RESPONSE` | praise, thanks, tease, boundary push directed at bot | hard/soft by addressedness | quote when target is not latest | social short reply |
| `AMBIENT_CONTRIBUTION` | ordinary group talk with useful contribution | coalesced soft slot | no quote by default | reply or silence after gate |
| `TASK_REQUEST` | explicit request requiring a capability | hard FIFO | quote anchor | capability router in later phase |

## Non-goals

- No percentage-based random quote or reaction selection.
- No persistent conversation-lane table.
- No second person-profile source of truth.
- No external agent framework dependency in Phase 1.

