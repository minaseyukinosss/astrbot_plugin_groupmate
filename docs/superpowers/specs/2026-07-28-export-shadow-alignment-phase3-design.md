# Export Shadow Alignment Phase 3 Design

## Goal

Build a read-only offline alignment tool that converts the QQChatExporter data in
`/Users/minase/Desktop/ams/学习素材/exports` into conservative behavior examples,
projects the current Groupmate mechanics over the same user evidence, and reports
where the target behavior and current architecture differ.

Phase 3 evaluates behavior mechanics only: whether to reply, trigger, interaction
scene, response act, quote behavior, media eligibility, run length, reply length,
and latency. It does not call a generation model, send messages, execute
capabilities, write memories, or learn Xiao Wei's identity and wording.

## Confirmed Data Source

The current source is a local QQChatExporter V5 chunked JSONL export. Its path and
target sender identifier remain local and are supplied at execution time through
`SHADOW_EXPORT_DIR` and `SHADOW_TARGET_UIN`:

- manifest total: 11,304 records;
- target records: 1,461;
- target record types include 1,000 text, 303 reply, 118 video, 35 forward,
  two file, one audio, and two system records.

These counts are input-integrity checks. They are not runtime reply or media
probabilities.

## Privacy Decision

The repository may contain code, synthetic fixtures, anonymous labels, and
aggregate statistics. It must not contain:

- target or participant QQ numbers and exporter UIDs;
- the real group name;
- raw chat text or target response text;
- media download paths, URLs, filenames, or hashes;
- a reversible mapping from anonymous IDs to exporter IDs.

Raw excerpts may appear only in a local review artifact under `eval/results/`,
which is ignored by Git. The normal machine-readable report contains anonymous
sample IDs and derived features only. Anonymous IDs are HMAC-SHA256 values derived
from exporter message IDs with a 32-byte local salt, never from message text. The
salt defaults to `eval/results/.shadow-id-salt`; it is generated once with a
cryptographically secure source, reused for stable local IDs, and never included
in a report or committed.

## Architecture

The tool is split into five read-only components:

```text
QQChatExporter manifest + chunks
    -> Export Ingest
    -> Opportunity And Reply-Run Extraction
    -> Independent Reference Labeler
    -> Current Mechanics Shadow Projector
    -> Behavior Diff And Local Reports
```

### Export Ingest

`eval.export_ingest` validates `manifest.json`, discovers every entry in
`chunked.chunks`, confines each `relativePath` to the export root, parses JSONL
records, and returns normalized immutable events. A normalized event
contains only the fields needed during the current process: exporter message ID,
sequence, timestamp, sender identity, type, plain text, element kinds, reply
reference, mention evidence, and media presence.

The ingest layer:

- verifies manifest and observed total record counts;
- validates the configured target UIN against observed sender records;
- orders records by timestamp, sequence, and message ID;
- retains the first behavior-equivalent duplicate message ID while counting
  duplicates, permits presentation-only exporter drift, and rejects conflicts in
  normalized behavioral fields;
- falls back from null, blank, or `"0"` reply references to a valid legacy
  message ID and retains only valid non-empty string mention identifiers;
- marks system and recalled messages as excluded rather than treating them as
  dialogue;
- rejects malformed JSON, missing required fields, invalid timestamps, and
  missing chunk files instead of silently producing partial conclusions.

No normalized raw event is written to a repository-tracked path.

### Opportunity Extraction

Each non-target, non-system content message is a potential user opportunity. The
extractor keeps a short preceding context window so the current scene and
addressee logic can be projected over the same evidence.

Target messages are grouped into response runs. A run contains consecutive target
content records with no intervening non-target content and no more than 15 seconds
between target records. A target record with a different explicit reply anchor
starts a new run even inside that time window.

Reply association uses conservative precedence:

1. An explicit QQ reply reference anchors the run to the referenced message.
2. Unquoted target output following a unique adjacent non-target message within
   20 seconds is high confidence when no competing user message intervenes.
3. A target-directed alias, native mention, or reply-to-target opportunity may
   anchor the next target run within 60 seconds when it is the only directed
   candidate in that interval.
4. Any run with multiple plausible sources, a missing reference, timestamp
   inversion, or conflicting anchors is sent to local review and is not used as
   automatic ground truth.

Messages in the context window of an ambiguous or linked response run are marked
`covered_context`; they are not counted as confident silence. Remaining user
opportunities with no linked target run are observed silence examples.

The extracted run records only derived output mechanics in the shareable model:
quoted or unquoted, media present, number of target messages, character-count
bucket, and response latency bucket. Exact response text remains local-only.

### Independent Reference Labeler

`eval.reference_labeler` produces reference `InteractionScene` and `ResponseAct`
labels without importing Groupmate's production trigger, scene, or planner
functions. This prevents a production rule from grading itself.

The labeler accepts only high-confidence evidence:

- bare alias, presence check, or greeting -> `ACKNOWLEDGE`;
- explicit thanks, praise, gift, or care -> `RECIPROCATE`;
- explicit light challenge or teasing -> `PLAYFUL_REPLY`;
- harassment, privacy extraction, coercion, or unsafe intimacy -> `BOUNDARY`;
- explicit task verb with a complete object -> task candidate;
- explicit task verb with a missing object -> `CLARIFY` candidate;
- incoming media with a lightweight request for reaction -> `VISUAL_REACTION`;
- direct question without a capability requirement -> `ANSWER`.

Task success, unsupported status, or handoff is labeled automatically only when
the export contains explicit observable evidence. Otherwise it enters review.
Conflicting cues and low-confidence semantics also enter review. Human-approved
labels can be supplied in a local overrides file keyed by anonymous sample ID;
the overrides file is not committed when it contains excerpts or exporter IDs.

### Alias Normalization

The target export contains target-specific aliases. Before current mechanics are
projected, those aliases are replaced only in the in-memory replay view with the
configured Aemeath alias. This makes the comparison about addressedness and scene
mechanics instead of literal product naming.

The original text is not modified on disk, copied into fixtures, or used to train
the Aemeath persona. Target worldview, relationship labels, catchphrases, and
response wording remain excluded.

### Current Mechanics Shadow Projector

`eval.shadow_projector` uses production read-only components over a synthetic
`TopicSnapshot` built from each opportunity context:

- `TriggerRouter`;
- `classify_scene` and scene quote policy;
- `AddresseeResolver`;
- `OpportunityArbiter` for reply/silence mechanics;
- `ReplyIntentPlanner` and `ResponseActPlan`;
- `ReactionPolicy` for decorative-media eligibility.

The projector does not instantiate `CognitiveWorkflow`. That prevents generation,
delivery, outbox, memory, capability, and host-agent side effects. Unsupported
external tasks are projected as handoff ownership or unsupported behavior from
explicit local rules; no external agent is invoked.

Each projection contains the trigger, would-reply flag, scene, primary act, quote
eligibility, decorative-media eligibility, target ambiguity, and reason codes.

## Difference Report

`eval.behavior_diff` compares reference examples and current projections. The JSON
report and human-readable Markdown report contain:

- source counts, exclusions, duplicates, linked runs, and review coverage;
- reply/silence confusion matrix;
- scene confusion matrix for high-confidence labels;
- response-act confusion matrix for high-confidence labels;
- quote mismatch counts by scene;
- media-eligibility mismatch counts by scene and act;
- target run-length, reply-length-bucket, and latency-bucket diagnostics;
- violation counts for boundary media, false task completion eligibility,
  ambiguous-target media, and multiple projected owners;
- anonymous sample IDs grouped by mismatch category;
- tool configuration and deterministic version identifiers.

Overall target ratios are diagnostics only. The report contains no
`runtime_probability`, sampling weight, or automatic configuration mutation.

## Command-Line Interface

`python3.7 -m eval.shadow_export` runs the complete local pipeline. Required
arguments are export directory and target UIN. Alias mapping and report paths are
explicit arguments. A representative invocation is:

```bash
python3.7 -m eval.shadow_export \
  --export-dir "$SHADOW_EXPORT_DIR" \
  --target-uin "$SHADOW_TARGET_UIN" \
  --target-alias 小维 \
  --current-alias 爱弥斯 \
  --id-salt-file eval/results/.shadow-id-salt \
  --output eval/results/phase3-shadow.json \
  --review-output eval/results/phase3-review.jsonl
```

The shareable JSON report excludes UINs and raw text. The local review JSONL may
contain the minimum source and response excerpts needed for human judgment and is
always written under an ignored path unless the user explicitly selects another
local path.

## Failure Behavior

- Missing or invalid manifest: fail before reading chunks.
- Missing chunk, malformed JSON, or invalid required field: fail with file and
  line location.
- Manifest/observed count mismatch: fail integrity verification.
- Configured target absent: fail with a count-only error.
- Duplicate ID: retain the first behavior-equivalent record, count it, permit
  presentation-only exporter drift, and fail when normalized behavioral fields
  disagree.
- Missing reply target or conflicting anchors: add a review item; do not guess.
- Invalid local override: fail with the anonymous sample ID and field name.
- Empty high-confidence set: produce ingestion counts and exit non-zero because no
  alignment conclusion is possible.
- Report write failure: leave source data untouched and exit non-zero.

## Testing

All automated tests use small synthetic QQChatExporter directories created in a
temporary path. No production export text or identifiers are copied into tests.

Required coverage:

- manifest and multi-chunk parsing;
- exact count validation and behavior-equivalent duplicate handling;
- explicit reply association;
- conservative adjacent association and ambiguous review fallback;
- target response-run grouping and split anchors;
- reference label confidence and review cases;
- alias normalization without modifying source events;
- production projector results for direct, social, task, boundary, visual, and
  ambient examples;
- confusion matrices and conditional quote/media metrics;
- deterministic JSON report ordering;
- privacy scanning of shareable reports;
- CLI success and malformed-export failure;
- existing 364-test suite and deterministic evaluation gates remain green.

## Acceptance Criteria

The first real-data run is accepted when:

1. ingestion verifies 11,304 total records and 1,461 target records;
2. malformed or ambiguous records are reported rather than silently guessed;
3. two runs over unchanged input with the same local salt produce identical
   anonymous labels and aggregate values;
4. the shareable report contains no raw UIN, UID, group name, chat text, media URL,
   filename, or content hash;
5. the local review queue provides enough context to resolve ambiguous labels;
6. the report separates scene/act conditions from overall drift statistics;
7. no generation provider, network call, delivery, memory write, capability, or
   runtime configuration mutation occurs;
8. the existing test suite and both deterministic evaluation corpora pass.

## Non-Goals

- Reproducing Xiao Wei's identity, worldview, names, catchphrases, or response
  text.
- Copying target images, videos, stickers, or other media assets.
- Automatically changing runtime thresholds or sampling from target percentages.
- Generating Aemeath replies or judging prose quality in this phase.
- Live shadow ingestion from AstrBot; that follows after offline alignment is
  reviewed.
- Uploading chat text to an external labeling model.
