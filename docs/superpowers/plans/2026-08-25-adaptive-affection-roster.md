# Adaptive Affection Roster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the affection leaderboard from the complete NapCat group roster and render complete, readable images for 1–10, 11–50, 51–100, and 100+ member groups.

**Architecture:** Make the affection query boundary asynchronous so it can call AstrBot `event.get_group()`, normalize the returned members, and merge them with Groupmate relationship projections while preserving a separate local recent-interaction count. Extend the leaderboard projection with roster completeness metadata, then let the presenter select a complete full-page layout for 820 / 1180 / 1380 px canvases.

> 2026-08-25 final visual revision: the user supplied a higher-fidelity six-column capsule reference after the original safe-viewport plan was written. Task 3's old adaptive-width details below are superseded by the final spec: fixed 1380 px and six columns at every group size, empty columns preserved for small groups, 240 members per page, no table headers, explicit dynamic viewport height, and AstrBot `full_page=True` without `clip`.

**Tech Stack:** Python 3, AstrBot `AstrMessageEvent`, aiocqhttp/NapCat OneBot group APIs through `event.get_group()`, SQLite participant directory, Jinja-compatible HTML/CSS, pytest.

---

## File map

- Modify `groupmate/adapters/astrbot_bridge.py`: asynchronous full-roster query and local fallback.
- Modify `groupmate/adapters/participants.py`: read recent observed actor IDs without mutating interaction timestamps.
- Modify `groupmate/social_runtime/society/affection_leaderboard.py`: expose member count, recent interaction count, and roster completeness.
- Modify `groupmate/adapters/affection_card.py`: adaptive pagination, safe dimensions, and small/medium/large layouts.
- Modify `main.py`: await the query and render only inside the declared safe canvas.
- Modify targeted tests in `tests/contracts/` and `tests/social_runtime/`.

### Task 1: Separate full roster from recent observed members

**Files:**
- Modify: `groupmate/adapters/participants.py`
- Modify: `groupmate/social_runtime/society/affection_leaderboard.py`
- Test: `tests/contracts/test_participants.py`
- Test: `tests/social_runtime/test_affection_leaderboard.py`

- [ ] **Step 1: Write failing participant and leaderboard tests**

Add a read-only recent actor ID assertion and verify the projection keeps roster and interaction counts separate:

```python
def test_recent_actor_ids_do_not_mutate_or_expand_the_directory(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")
    directory.remember(_event(actor_id="1001", received_at=100))
    directory.remember(_event(actor_id="1002", received_at=10))

    assert directory.recent_actor_ids(
        persona_id="persona", group_id="group", since=50
    ) == frozenset({"1001"})


def test_complete_roster_reports_members_and_recent_interactions_separately():
    result = AffectionLeaderboardService(FakeRelationships({})).build(
        persona_id="p",
        group_id="g",
        group_name="群",
        requester_id="1",
        members=(
            {"actor_id": "1", "display_name": "一", "updated_at": 100},
            {"actor_id": "2", "display_name": "二", "updated_at": 100},
            {"actor_id": "3", "display_name": "三", "updated_at": 100},
        ),
        updated_at=100,
        recent_active_count=1,
        roster_complete=True,
    )

    assert len(result.entries) == 3
    assert result.recent_active_count == 1
    assert result.roster_complete is True
    assert result.public_context()["member_count"] == 3
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/contracts/test_participants.py \
  tests/social_runtime/test_affection_leaderboard.py
```

Expected: failures for missing `recent_actor_ids` and unsupported leaderboard metadata arguments.

- [ ] **Step 3: Implement the read-only identity query and projection metadata**

Add this contract to `ParticipantDirectory`:

```python
def recent_actor_ids(
    self, *, persona_id: str, group_id: str, since: int
) -> frozenset[str]:
    with connect_database(self.path) as db:
        rows = db.execute(
            "SELECT actor_id FROM participant_directory "
            "WHERE persona_id=? AND group_id=? AND updated_at>=? AND actor_id<>''",
            (str(persona_id), str(group_id), max(0, int(since))),
        ).fetchall()
    return frozenset(str(row["actor_id"]) for row in rows)
```

Extend `AffectionLeaderboard` and `AffectionLeaderboardService.build` with:

```python
@dataclass(frozen=True)
class AffectionLeaderboard:
    group_id: str
    group_name: str
    updated_at: int
    entries: tuple[AffectionLeaderboardEntry, ...]
    recent_active_count: int = 0
    roster_complete: bool = True

# public_context
"member_count": len(self.entries),
"recent_active_count": self.recent_active_count,
"roster_complete": self.roster_complete,
```

Clamp `recent_active_count` to `0..len(entries)` and add “成员名单暂未完全同步” to the text fallback when `roster_complete` is false.

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/participants.py \
  groupmate/social_runtime/society/affection_leaderboard.py \
  tests/contracts/test_participants.py \
  tests/social_runtime/test_affection_leaderboard.py
git commit -m "feat: separate affection roster from activity"
```

### Task 2: Fetch the complete AstrBot/NapCat group roster

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `main.py`
- Test: `tests/contracts/test_affection_query.py`
- Test: `tests/shared/test_plugin_skeleton.py`

- [ ] **Step 1: Write failing full-roster and fallback tests**

Use an event test double with an async `get_group` method:

```python
class FakeMember:
    def __init__(self, user_id, nickname):
        self.user_id = user_id
        self.nickname = nickname


class FakeGroup:
    group_name = "完整测试群"
    members = (
        FakeMember("1001", "查询者"),
        FakeMember("1002", "沉默成员"),
        FakeMember("bot-1", "机器人"),
    )


class FakeEvent:
    message_str = "查看好感度"
    message_obj = SimpleNamespace(
        self_id="bot-1",
        raw_message={
            "message_id": "query-1",
            "group_id": "g-1",
            "group_name": "测试群",
            "user_id": "1001",
            "self_id": "bot-1",
            "time": 100,
            "sender": {"card": "查询者"},
            "message": [{"type": "text", "data": {"text": "查看好感度"}}],
        },
    )

    async def get_group(self, group_id=None, **kwargs):
        assert group_id == "g-1"
        return FakeGroup()
```

Assert that `await bridge.prepare_affection_query(event)` includes both human members, excludes the bot, reports one recent active member, and marks the roster complete. Add a second event whose `get_group` raises and assert the local roster remains usable with `roster_complete is False`.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/contracts/test_affection_query.py \
  tests/shared/test_plugin_skeleton.py
```

Expected: failures because the query is synchronous and does not call `get_group`.

- [ ] **Step 3: Implement async roster normalization and fallback**

Change the public contract:

```python
async def prepare_affection_query(self, event: object) -> AffectionQuery | None:
```

Before platform lookup, capture local data:

```python
recent_ids = participants.recent_actor_ids(
    persona_id=self.settings.persona_id,
    group_id=group_id,
    since=now - 30 * 24 * 60 * 60,
)
local_members = participants.active_members(...)
```

Call the platform with a bounded wait and normalize object or mapping members:

```python
group_getter = getattr(event, "get_group", None)
roster_complete = False
members = local_members
group_name = str(translated.payload.get("group_name") or "当前群聊")
if callable(group_getter):
    try:
        group = await asyncio.wait_for(group_getter(group_id), timeout=5.0)
        platform_members = self._normalize_group_members(
            group, bot_id=str(translated.payload.get("bot_id") or ""), now=now
        )
        if platform_members:
            members = platform_members
            roster_complete = True
            group_name = str(getattr(group, "group_name", "") or group_name)
    except Exception:
        pass
```

Do not write the full roster with `participants.remember_actor`, because that would make silent members appear recently active. Pass `recent_active_count=len(recent_ids - {bot_id})` and `roster_complete` into the leaderboard service.

In `main.py`, change:

```python
query = await self.bridge.prepare_affection_query(event)
```

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/astrbot_bridge.py main.py \
  tests/contracts/test_affection_query.py tests/shared/test_plugin_skeleton.py
git commit -m "feat: query complete group roster"
```

### Task 3: Implement safe adaptive layouts and pagination

**Files:**
- Modify: `groupmate/adapters/affection_card.py`
- Test: `tests/contracts/test_affection_card.py`

- [ ] **Step 1: Write failing layout-tier tests**

Add assertions covering all size boundaries:

```python
@pytest.mark.parametrize(
    ("count", "layout", "columns", "pages"),
    (
        (10, "small", 1, 1),
        (11, "medium", 2, 1),
        (50, "medium", 2, 1),
        (51, "large", 4, 1),
        (100, "large", 4, 1),
        (101, "paged", 4, 2),
        (228, "paged", 4, 3),
    ),
)
def test_presenter_selects_safe_adaptive_layout(count, layout, columns, pages):
    result = AffectionCardPresenter().pages(_board(count=count, requester_rank=count))
    assert len(result) == pages
    assert result[0].context["layout"] == layout
    assert result[0].context["column_count"] == columns
    assert result[0].context["render_width"] <= 760
    assert result[0].context["render_height"] <= 560
```

Also assert that `我的位置` is absent from the template, `你` remains, the header contains both `群成员` and `近 30 天互动`, and a 228-member requester page is sent first without duplicate members.

- [ ] **Step 2: Run the card tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/contracts/test_affection_card.py
```

Expected: failures for old page size, old widths, missing layout key, and duplicate requester summary.

- [ ] **Step 3: Implement layout selection and safe dimensions**

Use this policy in `AffectionCardPresenter`:

```python
page_size = 100 if len(leaderboard.entries) > 100 else len(leaderboard.entries)
layout = (
    "paged" if len(leaderboard.entries) > 100
    else "large" if len(leaderboard.entries) > 50
    else "medium" if len(leaderboard.entries) > 10
    else "small"
)
column_count = {"small": 1, "medium": 2, "large": 4, "paged": 4}[layout]
row_height = {"small": 42, "medium": 18, "large": 18, "paged": 18}[layout]
render_width = 720 if layout == "small" else 760
rows = max(1, math.ceil(len(entries) / column_count))
render_height = min(560, 92 + rows * row_height + (16 if page_count > 1 else 0))
```

Rebuild the template around one header and the actual ranked rows. Set `html`, `body`, and `main` to the exact declared width and height; use `overflow: hidden`; constrain all text with `min-width: 0`, ellipsis, or fixed grid tracks. Small rows use comfortable 14 px text and 42 px height. Medium and large rows use 10–11 px text, repeated column headers, and no pinned duplicate summary.

Keep the screenshot options in `main.py` clipped to `render_width` and `render_height`, now guaranteed below the platform viewport.

- [ ] **Step 4: Run card and composition tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/contracts/test_affection_card.py \
  tests/shared/test_plugin_skeleton.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Render boundary fixtures and inspect geometry**

Render 3, 10, 50, 100, and 228-member fixtures. For each page assert in the preview DOM:

```javascript
const root = document.documentElement;
const nodes = [...document.querySelectorAll("body *")];
const bounds = nodes.map(node => node.getBoundingClientRect());
({
  scrollWidth: root.scrollWidth,
  scrollHeight: root.scrollHeight,
  overflowRight: bounds.some(box => box.right > root.clientWidth + 0.5),
  overflowBottom: bounds.some(box => box.bottom > root.clientHeight + 0.5),
});
```

Expected: `scrollWidth <= render_width`, `scrollHeight <= render_height`, `overflowRight == false`, and `overflowBottom == false` for every page.

- [ ] **Step 6: Commit**

```bash
git add groupmate/adapters/affection_card.py \
  tests/contracts/test_affection_card.py
git commit -m "feat: adapt affection card to group size"
```

### Task 4: Final targeted verification and release package

**Files:**
- Modify: `metadata.yaml`
- Verify: `dist/astrbot_plugin_groupmate-1.0.0-rc.24.zip`

- [ ] **Step 1: Run the full targeted suite**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/contracts/test_participants.py \
  tests/social_runtime/test_affection_leaderboard.py \
  tests/contracts/test_affection_query.py \
  tests/contracts/test_affection_card.py \
  tests/shared/test_plugin_skeleton.py \
  tests/shared/test_astrbot_package_loading.py
git diff --check
```

Expected: all selected tests pass and `git diff --check` prints nothing.

- [ ] **Step 2: Bump and commit the release version**

Change `metadata.yaml` to `version: 1.0.0-rc.24`, then run:

```bash
git add metadata.yaml
git commit -m "chore: release 1.0.0-rc.24"
```

- [ ] **Step 3: Build and verify the AstrBot package**

```bash
git archive --format=zip \
  --prefix=astrbot_plugin_groupmate/ \
  -o dist/astrbot_plugin_groupmate-1.0.0-rc.24.zip HEAD
unzip -t dist/astrbot_plugin_groupmate-1.0.0-rc.24.zip
unzip -p dist/astrbot_plugin_groupmate-1.0.0-rc.24.zip \
  astrbot_plugin_groupmate/metadata.yaml
```

Expected: archive integrity passes and embedded metadata reports `1.0.0-rc.24`.
