# Affection Query Card Implementation Plan

**Goal:** 实现群内精确命令 `查看好感度`，不调用模型、不进入普通参与判断，并返回已确认的粉色公开榜单；渲染失败时返回查询者本人的文本结果。

**Architecture:** `ParticipantDirectory` 提供当前群近 30 天成员；纯领域服务读取已提交关系投影、排序并生成隐私裁剪的榜单数据。AstrBot 组合根在普通聊天链路前精确认领命令，调用宿主 `Star.html_render()` 生成图片并通过事件结果发送。查询路径不复用社交回复模型和 Outbox。

### Task 1: Build scoped leaderboard query

- Extend `ParticipantDirectory` with a scoped 30-day active-member query.
- Add immutable leaderboard entry/result contracts and sorting in `groupmate/social_runtime/society/affection_leaderboard.py`.
- Include the requester, exclude the known Bot ID, expose only QQ tail, score and stage in public rows.
- Test group isolation, active-window filtering, ordering and requester highlighting.

### Task 2: Build the confirmed pink card

- Add `groupmate/adapters/affection_card.py` with a fixed HTML/Jinja template.
- Render warm-white content on a soft pink background, a fixed “我的位置” strip, multi-column full ranking and duplicate requester highlight at the real rank.
- Split only above 240 members and put the requester page first.
- Keep all member-controlled strings escaped; provide a bounded text fallback.

### Task 3: Add exact command ownership

- Add a strict NFKC + trim matcher for exactly `查看好感度`.
- Have the bridge prepare the scoped query without calling cognition or the reply planner.
- In `main.py`, stop host propagation before rendering/sending, yield AstrBot image results, and return immediately.
- If rendering fails, yield the text fallback; never fall through to default LLM or Groupmate chat.

### Task 4: Validate with focused tests

- Run only leaderboard, participant, bridge/query ownership and small source-contract tests.
- Confirm no cognition invocation, no duplicate response ownership, private full IDs absent from render context, and fallback always exists.
- Run `git diff --check`; leave the existing untracked `analysis/` directory untouched.
