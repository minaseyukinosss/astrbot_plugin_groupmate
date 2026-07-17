# AstrBot Groupmate Agent Design

Date: 2026-07-17
Status: Approved for implementation
Target: AstrBot 4.24+ with QQ through NapCat/OneBot v11

## 1. Purpose

Build a reusable AstrBot plugin that acts as a group-chat companion rather than
a command bot. It observes group conversation, maintains bounded context,
reliably wakes when addressed, decides when a spontaneous contribution is
appropriate, and responds through the configured persona without flooding the
group.

The plugin ships with an Aemeath-oriented default preset, but its domain logic
must not hard-code Aemeath names, relationships, or phrasing.

## 2. Confirmed Product Decisions

- Platform: QQ through NapCat/OneBot v11.
- Context recovery: fetch the latest 100 messages at first contact or after a
  reconnect when the platform API is available, then continue with live events.
- Participation mode: balanced.
- Spontaneous contribution target: roughly 3-6 messages per active group per
  hour. Direct wake events do not consume this quota.
- Vision: analyze images only when the bot is addressed about an image or the
  active topic clearly depends on it.
- Reuse: generic engine with an Aemeath default configuration.
- Memory: lightweight persistent social memory, not a complete permanent chat
  archive.
- Commands: existing AstrBot and third-party plugin commands always take
  precedence. Groupmate observes but never duplicates or intercepts their
  output.
- Models: a small, independently configurable decision model gates spontaneous
  participation; the current group chat model produces user-visible replies.

## 3. Architecture Decision

Use an event-driven, single-agent cognitive workflow implemented as a modular
monolith with ports and adapters. Do not introduce a nested agent framework or
multi-agent orchestration in the first version.

This decision combines the useful parts of mature agent architectures:

- deterministic routing and composable workflows for predictable control;
- observation, retrieval, reflection, and planning for believable behavior;
- explicit graph state and checkpoints for replay and fault analysis;
- tiered memory for bounded prompts and long-term coherence;
- structured model outputs, guardrails, hooks, and tracing;
- an internal candidate-thought queue for proactive turn-taking.

The LLM does not own the runtime loop. Code owns scheduling, state transitions,
quotas, idempotency, and persistence. Models only perform bounded semantic
tasks behind typed interfaces.

## 4. System Boundaries

### 4.1 Platform Shell

The AstrBot plugin shell contains all required handlers and hooks:

- a group-message observer for AIOCQHTTP events;
- an `on_llm_request` hook that enriches native direct-wake requests;
- administrator commands for status, pause/resume, context reset, memory
  inspection, and decision diagnostics;
- startup and termination lifecycle handling.

The shell translates AstrBot objects into internal domain events. Domain
services must not import AstrBot types.

### 4.2 Per-Group Actor Runtime

Each group has a logical actor with a serialized mailbox. It owns:

- ordered message ingestion;
- the working context window;
- topic segmentation and debounce state;
- pending candidate thoughts;
- rate-limit state;
- a single in-flight decision or response;
- reliable outbound scheduling.

Groups run independently. Within one group, state mutations are serialized to
prevent duplicate decisions, out-of-order context, and quota races.

### 4.3 Cognitive Workflow

The explicit state sequence is:

1. `OBSERVE`: normalize and validate an incoming event.
2. `SEGMENT`: attach it to an active topic or start a new one.
3. `RECALL`: retrieve only relevant working and social memory.
4. `THINK`: create a concise candidate contribution, not hidden chain of
   thought.
5. `GATE`: apply deterministic policies and, when eligible, the decision model.
6. `PLAN`: select target message, conversational angle, modality, and timing.
7. `GENERATE`: call the response model with persona and bounded context.
8. `GUARD`: validate content and optionally perform one repair pass.
9. `SCHEDULE`: add a human-like delay and verify the contribution is still
   timely.
10. `SEND`: enqueue and deliver an idempotent outbound message.
11. `LEARN`: update approved social or episodic memory candidates.

Every transition emits a trace record with a stable `decision_id` and reason
codes. Raw model reasoning is never stored.

## 5. Trigger Routing

### 5.1 Native Direct Wake

AstrBot continues to own `@bot`, replies to a bot message, and configured wake
prefixes. The observer records the event but does not generate independently.
The `on_llm_request` hook adds bounded group context and relevant memories to
the existing request, so only one reply is produced.

### 5.2 Plugin Direct Wake

Configured aliases used as direct address or an explicit discussion about the
bot trigger the plugin response path immediately. Alias presence alone is not
sufficient: the router distinguishes direct address from unrelated homonyms.

### 5.3 Spontaneous Participation

Ordinary messages are accumulated into a topic window. Deterministic policies
first reject commands, bot messages, stale topics, cooldown violations,
duplicate content, empty media, and low-information noise.

Eligible topics are sent to the decision model. The response must validate
against this conceptual schema:

- `action`: `respond` or `ignore`;
- `confidence`: 0.0-1.0;
- `target_message_id`: optional message to answer;
- `reason_code`: closed enum;
- `contribution`: one-sentence description of what the bot can add;
- `needs_vision`: boolean;
- `urgency`: low, normal, or high.

Only a validated `respond` decision above the configured threshold advances.
Model failure or invalid output fails closed to silence.

### 5.4 Command Bypass

Messages recognized as wake-prefix commands or matching activated command
handlers never enter Groupmate generation. They remain visible in the event
log so later conversation can refer to the result, but Groupmate does not send
a second personality response.

## 6. Topic and Timing Model

- Default debounce: randomized 4-8 seconds.
- Maximum topic collection window: 12 seconds.
- Candidate thought TTL: 20 seconds by default.
- A newer direct wake supersedes any pending spontaneous contribution.
- A candidate is discarded if the topic changes, another participant already
  supplies the same answer, the target message is recalled, or its TTL expires.
- Human-like delay is based on output length and urgency, with a strict upper
  bound so direct replies do not feel unresponsive.
- At most one spontaneous response may be in flight per group.

## 7. Memory Model

Memory is a first-class subsystem behind a repository interface.

### 7.1 Event Log

Stores normalized recent events for audit, replay, and context recovery. It has
a configurable retention period and is not treated as permanent character
memory.

### 7.2 Working Memory

An in-memory ring of the latest 100 group messages. It is used for topic
understanding and direct request enrichment. It is reconstructed from the
event log and optional NapCat history.

### 7.3 Social Profiles

Stores stable, scoped facts such as QQ ID, current display name, configured
address, relationship label, and explicitly approved preferences. Manual
configuration has higher authority than learned data.

### 7.4 Episodic Memory

Stores a small number of recent events with source message IDs, confidence,
importance, creation time, and expiry time. Examples include a recently
mentioned exam or a game the member is currently playing.

### 7.5 Reflections

Periodic background consolidation may infer a higher-level summary only from
multiple consistent memories. Reflections cannot change protected identity or
relationship fields. Version one exposes the interface and conservative
consolidation rules; it does not run an unconstrained autonomous reflection
loop.

Retrieval combines namespace, relevance, recency, importance, confidence, and
authority. It returns a small token-bounded set, never the complete store.

## 8. Persona and Response Generation

Persona input is composed from four separately managed layers:

1. stable identity and world facts;
2. relationship and boundary policies;
3. language style and examples;
4. output constraints.

The plugin accepts an AstrBot persona selection when available and supports a
plugin-local persona text fallback. Dynamic context is passed as temporary
request content rather than appended to the stable system prompt.

The Aemeath preset derives its constraints from the supplied persona document:

- casual replies normally no longer than 30 Chinese characters;
- one sentence by default, two at most;
- no customer-service opening or forced follow-up question;
- no decision narration, stage directions, prompt discussion, or developer
  impersonation;
- direct boundaries instead of flirtation or self-deprecating appeasement;
- sparse world-building references;
- configured relationship-specific forms of address.

Training exports are retrieval examples only. They do not override persona
rules and are filtered to remove system messages, command output, duplicates,
long tool summaries, and known anti-examples.

## 9. Output Guardrails

Before sending, deterministic validators check:

- non-empty text and maximum length;
- sentence count and split-message count;
- forbidden narration and system vocabulary;
- customer-service templates and unwanted follow-up questions;
- duplicate or near-duplicate recent bot output;
- incorrect forms of address;
- accidental command or internal-ID leakage;
- stale topic and rate-limit state.

One bounded repair call is allowed for style-only violations. Safety, stale
context, duplicate, invalid target, or quota violations fail closed without a
reply.

## 10. Persistence and Reliability

SQLite is the default backend because the plugin is local, single-process, and
write volume is modest. Repositories isolate storage so PostgreSQL or another
backend can be introduced later.

Primary records are:

- normalized messages;
- group runtime snapshots;
- decisions and state transitions;
- social profiles;
- episodic memories and reflections;
- outbound messages.

Outbound messages use an outbox with a unique `decision_id`. Delivery is
idempotent. Stale chat replies are cancelled after reconnect rather than sent
late. Schema migrations are explicit and versioned.

## 11. Ports and Extension Points

The domain layer depends on protocols rather than implementations:

- `PlatformPort`
- `HistoryPort`
- `DecisionModelPort`
- `GenerationModelPort`
- `VisionPort`
- `MemoryRepository`
- `TriggerPolicy`
- `PersonaProvider`
- `OutputGuard`
- `MessageScheduler`
- `TraceSink`
- `Clock`

Extension registries allow additional trigger policies, memory rankers,
guardrails, media analyzers, and output transforms without editing the
orchestrator. Multi-agent delegation may later be added as a specialized tool,
but it is not part of the core chat loop.

## 12. Configuration Surface

The AstrBot WebUI schema exposes:

- enabled groups and bot aliases;
- decision and generation provider selections;
- persona selection or fallback persona text;
- participation mode, thresholds, quotas, cooldown, debounce, and quiet hours;
- history size and retention;
- vision enablement and provider;
- social relationship entries;
- memory retention and reflection controls;
- diagnostics and privacy-sensitive trace settings.

Defaults are conservative and usable. Secrets are not stored by this plugin;
provider credentials remain managed by AstrBot.

## 13. Failure Handling

- NapCat history unavailable: continue with live events and retry later.
- Decision model timeout or invalid schema: remain silent.
- Generation model failure on native direct wake: allow AstrBot's normal error
  behavior; plugin-direct wake logs the failure without a synthetic persona
  reply.
- Vision failure: continue only if text context is sufficient; otherwise remain
  silent.
- Database busy or transient write failure: retry boundedly and preserve
  in-memory operation; never duplicate send.
- Plugin reload or shutdown: cancel debounce tasks, flush snapshots, and close
  storage cleanly.

## 14. Observability and Evaluation

Every candidate receives traceable reason codes through the workflow. Admins
can inspect why the bot replied or stayed silent without exposing private model
reasoning.

Offline evaluation replays labeled group-message windows and measures:

- hard-trigger recall;
- spontaneous-response precision;
- inappropriate interruption rate;
- duplicate response rate;
- command interference rate;
- persona constraint pass rate;
- average decision and reply latency;
- model calls and estimated cost per 1,000 messages.

The supplied bot-only exports can evaluate style but cannot evaluate trigger
accuracy. Trigger evaluation requires paired or full-group conversation logs.

## 15. Testing Strategy

- Unit tests cover normalization, trigger classification, topic segmentation,
  quota calculations, memory ranking, guardrails, and state transitions.
- Contract tests use fake model, history, vision, storage, and platform ports.
- Integration tests exercise AstrBot event translation and native/direct wake
  coexistence without a live QQ account.
- Replay tests cover message bursts, topic changes, duplicate events, command
  bypass, reconnect history, recalls, model failures, and plugin shutdown.
- Persona regression tests use curated positive and negative examples from the
  supplied material.

## 16. First-Version Scope

Version one includes:

- AIOCQHTTP group observation;
- per-group actor runtime;
- latest-100 working context and best-effort NapCat backfill;
- native and alias wake routing;
- topic batching and structured decision gating;
- on-demand image analysis interface;
- response generation and Aemeath guardrails;
- SQLite social and episodic memory;
- reliable outbound delivery;
- WebUI configuration, admin diagnostics, and automated tests.

Version one excludes speech/video understanding, a vector database, scheduled
unsolicited check-ins, autonomous tool-use loops, multi-bot coordination, and
multi-agent orchestration.

## 17. Success Criteria

- Existing commands behave exactly as before and never receive a duplicate
  Groupmate response.
- `@`, reply-to-bot, and explicit aliases reliably wake the bot.
- In balanced mode, active groups receive approximately 3-6 spontaneous
  contributions per hour without consecutive flooding.
- Decision or vision outages do not cause unsolicited fallback chatter.
- Duplicate and out-of-order OneBot events do not produce duplicate replies.
- Restart restores bounded context and persistent social memory.
- The Aemeath preset passes its deterministic persona constraints.
- Core workflow tests run without AstrBot, NapCat, or network access.

