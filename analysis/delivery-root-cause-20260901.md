# Groupmate 零投递根因报告与修复任务书

**日期**：2026-09-01
**数据来源**：线上生产库快照 `groupmate-social-runtime-v2.db`（137 MB，56 张表，`PRAGMA quick_check` = ok）
**数据窗口**：2026-08-27 16:50:50 ~ 2026-09-01 11:00:53（约 5 天）
**代码基线**：`HEAD = 94824e3`，工作区已含 4.1 / 4.2 修复（未上线）

---

## 0. 给执行者的话

这份报告**取代** `effectiveness-fix-plan-20260831.md` 里的 4.3 和 4.4。那两节基于 08-24 的旧快照和静态推理，现已被生产数据证伪，不要执行。

4.1 和 4.2 的修复是正确的，保留。但它们的价值需要重新理解：**不是省钱，是抢回投递窗口**。原因见第 3 节。

本报告的所有结论都来自生产库的聚合查询，每条都附可复现的 SQL。请先自己跑一遍验证，再动手改代码。

---

## 1. 一句话结论

**5 天内 bot 一条消息都没有发出去。** 不是"发得少"，是 0 条。

两条回复文本已经完整生成，停在 `outbox` 里状态为 `expired`，`receipt_json` 为 `NULL`，从未调用平台发送接口。

根因不是决策太保守，而是：**候选意图的 30 秒 TTL 从认知开始计时，而端到端要 48 秒，回复计划生成出来时寿命只剩 2 到 9 秒，投递环节拿到它时已经过期。**

---

## 2. 实测漏斗

只统计已启用的主群 `1104337754`（另一启用群 `912113397` 仅 5 条消息，忽略）。

| 环节 | 数量 | 说明 |
|------|------|------|
| 入站消息 | 2488 | 100% 进入 `inbox`，无丢失 |
| 产生决策 | 1169 | AMBIENT 窗口合并，约 2.1 条消息一帧，合理 |
| Governor 判 ACT | 11 | 0.94% |
| 生成出回复计划 | 2 | 其余 8 条静默失败，1 条 `social_move_silence` |
| 提交到 outbox | 2 | 两条都 `expired` |
| **实际投递** | **0** | `receipt_json` 全为 NULL |

复现：

```sql
SELECT
  (SELECT COUNT(*) FROM inbox WHERE group_id='1104337754') AS inbox_rows,
  (SELECT COUNT(*) FROM governor_results) AS decisions,
  (SELECT COUNT(*) FROM reply_plans) AS plans,
  (SELECT COUNT(*) FROM outbox) AS parts,
  (SELECT COUNT(*) FROM outbox WHERE receipt_json IS NOT NULL) AS delivered;
```

预期输出：`2491 | 1169 | 2 | 2 | 0`

---

## 3. 根因 A（P0）：意图 TTL 从认知开始计时，端到端延迟吃光投递窗口

### 3.1 证据

生产库里两条计划的完整寿命：

```sql
SELECT plan_id,
  datetime(created_at,'unixepoch','localtime') AS created_local,
  datetime(expires_at,'unixepoch','localtime') AS expires_local,
  expires_at - created_at AS ttl_seconds,
  status
FROM reply_plans;
```

实测结果：

| plan_id | created | expires | TTL | status |
|---------|---------|---------|-----|--------|
| `reply:46640655…` | 08-29 13:38:34 | 08-29 13:38:36 | **2 秒** | `enqueued` |
| `reply:780c8aa8…` | 08-29 23:45:05 | 08-29 23:45:14 | **9 秒** | `enqueued` |

对应的 outbox part：

```sql
SELECT part_id, datetime(expires_at,'unixepoch','localtime') AS expires_local, status,
       receipt_json IS NULL AS no_receipt
FROM outbox;
```

两条都是 `expired` + `no_receipt=1`。

### 3.2 因果链

`groupmate/social_runtime/participation.py:123` 与 `:157`，候选意图创建时：

```python
expires_at=int(now) + 30,
```

这里的 `now` 是**认知开始时刻** T0，所以意图的绝对死期是 `T0 + 30`。

`groupmate/social_runtime/replying.py:476`，回复计划继承这个绝对死期：

```python
expires_at = min(int(selected.expires_at), now + 30)
```

此时 `now = T0 + 已耗时`。`min` 的左项恒为 `T0 + 30`，右项为 `T0 + 已耗时 + 30`，所以结果**恒等于 `T0 + 30`**。

于是计划的实际寿命是 `30 - 已耗时`。

`groupmate/social_runtime/replying.py:1550` 与 `:1560`，outbox part 和 bundle 直接沿用同一死期：

```python
expires_at=plan.expires_at,
```

`groupmate/social_runtime/delivery/outbox.py:439`，`claim_ready` 每次先做过期清理：

```python
@staticmethod
def _expire_ready(db, now: int) -> None:
    rows = db.execute(
        "SELECT part_id, payload_json FROM outbox "
        "WHERE status IN ('planned','ready') AND expires_at<=?",
        (now,),
    ).fetchall()
```

清理之后才查 `WHERE outbox.status='ready'`（`outbox.py:222`）。part 已被标 `expired`，查不到，`dispatch_next` 返回 `None`，投递静默结束。

### 3.3 为什么必然发生

端到端耗时实测：

```sql
WITH lat AS (
  SELECT CAST(completed_at - json_extract(
           evidence_json, '$.evaluation.source_event.occurred_at') AS INTEGER) AS sec
  FROM shadow_capture_evidence
  WHERE json_extract(evidence_json, '$.evaluation.frame.trigger_kind') = 'AMBIENT'
)
SELECT COUNT(*) n, MIN(sec) min_s, CAST(AVG(sec) AS INT) avg_s, MAX(sec) max_s FROM lat;
```

实测：`1167 | 4 | 48 | 191`

**平均 48 秒，而 TTL 只有 30 秒。** 那两条能活下来的计划对应端到端 28 秒和 21 秒，是分布里最快的极少数，即便如此也只剩 2 到 9 秒，仍然没赶上投递。

注意认知模型本身很快：

```sql
SELECT json_extract(d.value,'$.worker') AS worker,
       json_extract(d.value,'$.status') AS status,
       COUNT(*) n,
       ROUND(AVG(json_extract(d.value,'$.latency_ms')),0) avg_ms
FROM shadow_capture_evidence e,
     json_each(e.evidence_json,'$.evaluation.cognition_diagnostics') d
GROUP BY 1,2 ORDER BY n DESC;
```

实测：`level0.rules` 1169 次全成功平均 0 ms；`ambient_social_assessor` 1166 次成功平均 **1203 ms**，仅 1 次超时。

所以 48 秒里模型只占 1.2 秒，其余全是 AMBIENT 等待窗与串行编排。这正是 4.1 的价值所在：它省掉的那次沉默路径模型往返，直接换成投递窗口的余量。

### 3.4 修复方向

不要简单把 30 改大，那只是把问题推后。要решить的是"计时起点"和"预算归属"：

1. **区分决策有效期与投递有效期。** 意图的 30 秒约束的是"这个社交判断是否还成立"，不该同时约束"生成好的文本能否发出"。建议在 `ReplyPlan` 上引入独立的投递宽限期，例如计划落库时刻起算的固定窗口（建议 15 到 20 秒），与意图死期取**较晚者**而非较早者，同时保留一条硬上限防止发送远古消息。

2. **投递不应依赖下一次事件驱动。** 目前 `_dispatch_ready` 只在 `astrbot_bridge.py:2111` 被调用一次，位于回复生成的 `try` 块内。若该路径提前返回或抛异常，已入队的 part 就再无人认领，只能等过期。建议让 `_attention_wakeup_loop` 也周期性调用 `_dispatch_ready`，使投递具备独立于消息流的推进能力。

3. **先量出预算再定数值。** 改之前请先输出 T0 到各阶段的耗时分解（认知完成、场景解析完成、生成完成、入队完成），用真实分位数决定宽限期，不要凭直觉取整数。

### 3.5 验收

一个测试即可：构造端到端耗时超过意图 TTL 的场景，断言 part 仍能被 `claim_ready` 认领并拿到 receipt。不需要补全套延迟测试。

---

## 4. 根因 B（P1）：ACT 之后 73% 静默失败，且异常被吞

### 4.1 证据

11 次 ACT 的落点分布：

```sql
SELECT json_extract(state_json,'$.decision.participation_lane') AS lane,
       json_extract(state_json,'$.delivery.status') AS status,
       json_extract(state_json,'$.decision.reply_diagnostic') AS diag,
       COUNT(*) n
FROM message_traces
WHERE json_extract(state_json,'$.decision.outcome')='ACT'
GROUP BY 1,2,3;
```

实测：

| lane | status | diagnostic | n |
|------|--------|-----------|---|
| AMBIENT | PLANNING | `NULL` | 6 |
| DIRECT_FAST | PLANNING | `NULL` | 2 |
| AMBIENT | READY | `NULL` | 2 |
| AMBIENT | SILENT | `social_move_silence` | 1 |

8 条停在 `PLANNING`，最后一个 stage 是 `DECIDED`，**连 `PLANNED` 都没到，且 `reply_diagnostic` 为 NULL**。

时间跨度 08-27 19:54 到 08-31 15:14，贯穿全窗口，不是早期已修的遗留问题。

### 4.2 为什么诊断是空的

`groupmate/adapters/astrbot_bridge.py:2111-2114`：

```python
                    await self._dispatch_ready()
                    self.reply_error = None
                except Exception as exc:
                    self.reply_error = f"{type(exc).__name__}: {exc}"
```

整段回复生成与投递包在一个 `try` 里，异常只写进内存字段 `self.reply_error`，**不落库、不写 trace 诊断、不区分失败阶段**。进程重启即丢失。

对比 `social_move_silence` 那条：它走了正常的 silence 分支，所以有诊断码。8 条 `PLANNING` 则是异常或提前返回路径，什么都没留下。

### 4.3 修复方向

不要急着猜是哪一步失败。先让失败可见：

1. 在 `_handle_evaluations` 的回复生成段内，为每个可能提前返回的分支补一个结构化诊断码，写入 `message_traces` 的 `decision.reply_diagnostic`。
2. `except` 分支必须落库，至少记录异常类型和所处阶段（场景解析 / 立场 / 社交动作 / 生成 / 入队 / 投递）。
3. 阶段划分建议与 `stages` 数组对齐，使 `last_kind` 能直接指出断点。

做完这一步再跑一天，用真实诊断码分布决定后续修什么。**在诊断补齐之前不要改生成逻辑。**

### 4.4 验收

无需新增测试。上线后能用一条 SQL 查出 8 条失败分别属于哪个阶段即可：

```sql
SELECT json_extract(state_json,'$.decision.reply_diagnostic') AS diag, COUNT(*) n
FROM message_traces
WHERE json_extract(state_json,'$.decision.outcome')='ACT'
GROUP BY 1 ORDER BY n DESC;
```

---

## 5. 观测缺陷（P2）：未启用群产生永久 PENDING trace

### 5.1 证据

```sql
SELECT t.group_id,
  SUM(CASE WHEN EXISTS (SELECT 1 FROM inbox i WHERE i.event_id=t.event_id) THEN 1 ELSE 0 END) in_inbox,
  SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM inbox i WHERE i.event_id=t.event_id) THEN 1 ELSE 0 END) not_in_inbox
FROM message_traces t GROUP BY 1;
```

实测：

| group_id | in_inbox | not_in_inbox |
|----------|----------|--------------|
| `1104337754` | 2488 | **0** |
| `912113397` | 5 | **0** |
| `991220607` | 0 | 1016 |
| `1083302883` | 0 | 665 |
| `321697384` | 0 | 14 |

### 5.2 结论

**已启用的群零丢失。** 1695 条"未入库"全部来自 3 个未启用的群，`manager.ingest`（`manager.py:557`）按设计返回 `None`，这是正确行为。

真正的缺陷只是：`astrbot_bridge.py:1093` 的 `record_received` 和 `:1101` 的 `mark_entered` 对未启用群也建了 trace，这些 trace 永远停在 `ROUTED`，`decision.outcome` 恒为 `PENDING`，占全部 trace 的 40.5%，污染所有基于 trace 的统计。

### 5.3 修复方向

要么不为未启用群建 trace，要么给它们一个终结状态（例如 `NOT_ENABLED`）。优先级最低，但建议顺手做掉，否则后续每次看数据都要先手工排除。

---

## 6. 撤销 4.3 与 4.4：证伪依据

### 6.1 撤销 4.3「快车道因话题归类失败而静默失效」

原假设：被 @ / 被回复的消息可能完全不产生候选。

实测反驳：

```sql
SELECT json_extract(envelope_json,'$.payload.mentions_bot') AS mentions_bot,
       json_extract(envelope_json,'$.payload.address_kind') AS address_kind,
       COUNT(*) n
FROM inbox GROUP BY 1,2 ORDER BY n DESC;
```

实测：2496 条入站消息里 `mentions_bot=1` 的只有 **2 条**，且这 2 条**全部**走通 `DIRECT_FAST` 并产出候选、判定 ACT。快车道识别率 2/2。

这个 bug 在生产数据里不存在，改它收益为零。

### 6.2 撤销 4.4「认知降级的粒度过粗」

原假设：任一降级来源触发即全盘降级，导致 `force_observe`。

实测反驳：见 3.3 的 worker 统计，1169 次认知里 `level0.rules` 与 `ambient_social_assessor` 几乎全部 `SUCCEEDED`，仅 1 次超时。

进一步确认 `forced_observe` 的实际来源：

```sql
SELECT json_extract(obs.value,'$.proposition.decision') AS decision,
       json_extract(obs.value,'$.proposition.should_participate') AS speak,
       COUNT(*) n
FROM shadow_capture_evidence e,
     json_each(e.evidence_json,'$.evaluation.cognitive_observations') obs
WHERE json_extract(obs.value,'$.kind')='participation_assessment'
GROUP BY 1,2;
```

实测：`silence / 0 → 1163`，`speak / 1 → 3`。

**认知层根本没在降级。** `forced_observe` 走的是 `manager.py:1192` 的第二个条件（参与策略不允许），而参与策略不允许是因为模型明确判定不该说话。4.4 修的是不存在的问题。

---

## 7. 不要动的东西

### 7.1 不要调松 AMBIENT 的参与阈值

模型判定 silence 的代价指标分布：

```sql
SELECT json_extract(obs.value,'$.proposition.should_participate') AS speak,
       COUNT(*) n,
       ROUND(AVG(CAST(json_extract(obs.value,'$.proposition.disruption_cost') AS REAL)),2) avg_disrupt,
       ROUND(AVG(CAST(json_extract(obs.value,'$.proposition.novelty') AS REAL)),2) avg_novelty,
       ROUND(AVG(CAST(json_extract(obs.value,'$.proposition.target_confidence') AS REAL)),2) avg_target
FROM shadow_capture_evidence e,
     json_each(e.evidence_json,'$.evaluation.cognitive_observations') obs
WHERE json_extract(obs.value,'$.kind')='participation_assessment'
GROUP BY 1;
```

实测：silence 组 `disruption_cost` 均值 0.72、`novelty` 均值 0.11、`target_confidence` 恒为 0.0；speak 组分别为 0.20、0.30、0.85。

模型的判断是合理的：绝大多数群消息确实不是对 bot 说的，也确实没有插话价值。

对照参考 Bot 的行为分布：连续对话约一半、被点名或被回复约四分之一、自主定向与环境加入合计约四分之一、主动开场极少。

**参考 Bot 的环境介入本身只占少数。** 调松 AMBIENT 频率不会让 bot 更像群友，只会让它变成一个乱插话的机器人。当前 silence 率高不是缺陷。

### 7.2 现在不要碰冷启动

存在一个结构性死锁，但它是上述缺陷的**结果**，不是原因：

bot 从未发言 → 群友不知道它能对话 → 5 天只 @ 了它 2 次 → `DIRECT_FAST` 无输入 → `CONTINUATION` 触发 0 次（`conversation_leases` 表在库中不存在，从未建立过对话租约）→ 只剩最难通过的 AMBIENT → 继续不发言。

目标 bot 75.5% 的发言来自"连续对话 + 被点名"这两条确定性快车道，前提是群友愿意跟它说话。

**先把投递修通。** 等 bot 能稳定发出话，快车道自然会有流量，那时再评估是否需要冷启动策略。现在讨论冷启动是本末倒置。

---

## 8. 执行顺序

| 顺序 | 任务 | 理由 |
|------|------|------|
| 1 | 第 4 节：补齐失败诊断 | 先让失败可见，且不改行为，风险最低 |
| 2 | 第 3 节：修 TTL 与投递推进 | 唯一能把投递从 0 变成非 0 的改动 |
| 3 | 上线观测一天 | 用真实诊断码分布决定下一步 |
| 4 | 第 5 节：清理 trace 污染 | 纯观测改善，可随时做 |

4.1 与 4.2 的已有改动保持不动，随本轮一起上线。

**第 3 步完成前不要开始任何结构性重构**（`structural-refactor-plan-20260901.md` 继续搁置）。

---

## 9. 测试要求

按既有约定，只保留必要的：

- 第 3 节：一个测试，端到端耗时超过意图 TTL 时 part 仍可投递。
- 第 4 节：不新增测试，用 SQL 验证诊断码可见。
- 第 5 节：不新增测试。

不要为本报告的任何结论补写回归测试套件。现有 801 个测试全绿，保持绿即可。

---

## 10. 复现本报告

```bash
# 解压位置
cd analysis/shadow_live_20260901

# 只读方式跑既有审计脚本
sqlite3 'file:./groupmate-social-runtime-v2.db?mode=ro&immutable=1' \
  < ../shadow_20260824.sql
```

该脚本的第 64 到 71 行统计 `scene_work_requests` 的 `reason_code` 分布，是判断 4.2 上线效果的主要指标。当前基线为 `newer_scene_committed` 1325 条、`accepted` 1168 条、`attention_deadline_expired` 3 条。4.2 上线后 `newer_scene_committed` 应显著下降。
