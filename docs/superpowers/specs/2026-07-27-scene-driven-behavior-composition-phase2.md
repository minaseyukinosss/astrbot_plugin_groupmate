# Scene-Driven Behavior Composition Phase 2 Design

## Goal

Build the second behavior layer needed to approach the target QQ bot's interaction
effect: after Phase 1 identifies the interaction scene, select a concrete response
act, execute only supported capabilities, and compose text and media into one
persona-consistent delivery.

The product identity remains Aemeath. Target exports provide interaction mechanics
and evaluation evidence only. They do not provide Aemeath's identity, worldview,
catchphrases, relationship labels, or reusable response text.

## Evidence And Interpretation

The full-group export contains 1,461 messages from target QQ `323537051`. It mixes
natural dialogue, plugin output, media results, and automatic notices, so raw media
and reply counts cannot be runtime probabilities.

The strict dialogue analysis established the following reference observations:

- 605 natural dialogue cases were identifiable from the export.
- Natural replies are short: median 21 characters and p90 49 characters.
- 46.6% of strict dialogue replies visibly quote a source message.
- 39.0% of strict dialogue replies contain inline media.
- 86.4% of dialogue runs contain one natural dialogue message.
- Only 3.0% of dialogue runs address two or more users.

These values are validation signals conditioned on user interaction scenes. They
must never be sampled directly. For example, a task result containing an image and
a playful sticker reaction are different response acts even though both count as
media in an aggregate report.

## Architecture Decision

Use a typed, scene-driven pipeline:

```text
ChatMessage + InteractionScene + TargetingDecision
    -> ResponseActPlanner
    -> CapabilityRouter (optional)
    -> ResponseComposer
    -> existing DeliveryService
```

Phase 1 remains authoritative for trigger priority, hard-turn ordering,
continuation ownership, and quote anchoring. Phase 2 does not reclassify scheduling
or add random behavior selection.

### Response Act Planner

`ResponseActPlanner` deterministically projects observable evidence into one
primary act:

| Response act | Evidence | Default output |
|---|---|---|
| `ACKNOWLEDGE` | name-only call, greeting, presence check | one short acknowledgement |
| `ANSWER` | question that can be answered conversationally | concise answer |
| `CLARIFY` | task or question lacks a required object | one specific clarification |
| `RECIPROCATE` | thanks, praise, gift, care, greeting directed at the bot | short social response; reaction media may be eligible |
| `PLAYFUL_REPLY` | light teasing or playful challenge directed at the bot | short persona-consistent counter; reaction media may be eligible |
| `BOUNDARY` | harassment, privacy request, coercion, or unsafe request | short text boundary; no decorative media |
| `TASK_HANDOFF` | explicit request matches a registered capability | capability execution followed by persona composition |
| `TASK_UNSUPPORTED` | explicit request has no safe registered capability | honest limitation or host handoff; never claim completion |
| `VISUAL_REACTION` | incoming image/sticker invites a lightweight reaction | short text, reaction media, or both |

Precedence is safety-sensitive: `BOUNDARY` first, then explicit task handling,
clarification, direct question, social acts, visual reaction, and acknowledgement.
Only one primary act is selected per anchored turn. Supporting metadata can record
secondary cues without producing multiple competing replies.

### Capability Contracts

Create a static registry rather than dynamic plugin discovery. A capability
declares:

- stable name and supported request kinds;
- input requirements and whether media is required;
- deadline and failure policy;
- whether results may contain text, image, or both;
- a structured result with status, user-facing facts, media candidates, and trace
  metadata.

Capabilities cannot send messages, mutate social state, or write long-term memory.
They return data to the parent workflow. The main Aemeath persona owns the final
wording, guard checks, quote choice, and delivery.

Phase 2 initially registers adapters for existing vision and external-knowledge
paths. It also defines a host-agent handoff result for supported AstrBot Agent
requests. Broad tool installation, write-capable tools, recursive agents, and
automatic capability scanning are outside this phase.

### Response Composer

`ResponseComposer` converts the act, optional capability result, scene policy, and
persona-generated text into a structured draft:

- zero or one text segment group;
- zero or one selected reaction media item;
- zero or more task-result media items supplied by a capability;
- Phase 1 quote anchor;
- traceable act and capability identifiers.

The normal dialogue shape is one delivery. Multiple messages are allowed only when
a task result has distinct artifacts that cannot be represented coherently in one
chain. The composer does not introduce multi-user conversation lanes.

### Scene-Conditional Media Policy

Media eligibility is rule-based:

| Situation | Policy |
|---|---|
| directed thanks, praise, gift, greeting, or light teasing | reaction candidate allowed |
| incoming sticker/image with a clear lightweight social cue | visual reaction candidate allowed |
| ordinary ambient contribution | no media unless the selected act explicitly justifies it |
| help answer, clarification, serious topic | text by default |
| boundary, privacy, safety, ambiguous target | decorative reaction forbidden |
| image generation, image edit, lookup with media result | capability media allowed |

Reaction selection is semantic, not random. Candidates carry tags, safety state,
source, and stable identity. Selection requires tag compatibility, rejects unsafe
or unknown assets, and excludes recently delivered identities. With no eligible
candidate, composition falls back to text without changing the response act.

Phase 2 provides the catalog and selection contracts plus a configured local
catalog adapter. It does not copy target bot assets. Aemeath reaction assets must be
owned or explicitly configured by the operator.

## Data Flow

1. Runtime anchors the turn and Phase 1 classifies its `InteractionScene`.
2. Addressee resolution supplies reply audience and ambiguity evidence.
3. `ResponseActPlanner` selects one act and records reason codes.
4. For `TASK_HANDOFF`, the registry resolves one capability and executes it within
   a deadline. Unsupported or failed execution becomes an explicit result state.
5. The generation prompt receives the act, user-visible capability facts, and
   Aemeath persona context. Internal tool metadata is never exposed.
6. The output firewall validates text under act-specific length and safety rules.
7. `ResponseComposer` applies the scene media policy and quote anchor.
8. The existing outbox and `DeliveryService` send all text and media through the
   same terminal-state discipline.

## Failure Behavior

- Planner ambiguity selects `CLARIFY` or safe text, not a guessed capability.
- Missing capability selects `TASK_UNSUPPORTED` and never fabricates a result.
- Capability timeout or error is traced and converted to a concise failure or
  retry-later response when the anchored opportunity is still valid.
- Reaction catalog failure silently falls back to text.
- Capability media that fails safety validation is omitted; the text result may
  still be delivered.
- A host-agent handoff must suppress the Groupmate delivery path until a single
  owner is known, preventing double replies.
- All Phase 2 failures leave Phase 1 scheduling, ledger ingestion, and later turns
  operational.

## Compatibility And Rollback

- Existing text-only `DeliveryPlan` remains valid.
- Existing `VisionPort` and external-knowledge behavior are wrapped by adapters
  before their old direct paths are removed.
- Each registered capability and reaction catalog has an independent feature flag.
- Disabling Phase 2 composition restores the current text-only workflow without a
  database rollback.
- Any schema extension is additive and readable by the current ledger code.

## Evaluation

Evaluation is grouped by `InteractionScene` and `ResponseAct`. Overall export
ratios are drift indicators only.

Required checks:

- primary-act accuracy on labeled direct, social, task, boundary, and visual cases;
- zero false completion claims for unsupported or failed tasks;
- zero decorative media in boundary, privacy, and ambiguous-target cases;
- reaction semantic-match and recent-duplicate rejection;
- capability timeout does not block ingestion or later hard turns;
- one confirmed delivery produces one ledger/outbox result;
- Aemeath identity, relationship rules, and voice remain intact;
- direct and social dialogue remain near the target short-reply distribution;
- scene-conditional quote behavior from Phase 1 does not regress.

## Non-Goals

- Copying Xiao Wei's name, worldview, catchphrases, relationship labels, or media
  library.
- Percentage-based random replies, quotes, reaction media, or multi-message runs.
- Dynamic capability discovery, MCP marketplace, or unrestricted tool execution.
- Write-capable autonomous agents, recursive subagents, or true proactive tasks.
- A second persona or memory source of truth.
