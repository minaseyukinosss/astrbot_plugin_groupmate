# Social Runtime v2 灾难恢复手册

本手册只适用于 `groupmate-social-runtime-v2.db`，不构成生产验收或真实 QQ 发送授权。旧 `groupmate.db`、旧配置和 V2 不兼容：不得迁移、读取或把 V2 状态写回旧数据库。

## 恢复原则

- 先暂停所有目标群并停止新事件进入，再制作备份；保留原库只读现场。
- 使用 SQLite online backup，不能只复制 WAL 模式下的主 `.db` 文件。
- Event/Journal 是因果来源，Snapshot 只加速恢复；Projection 可从 Journal 重建。
- `SENT` 已有成功回执，`UNKNOWN` 可能已送达；两者均不得重发。遗留 `SENDING` 只能由现有 `OutboxService.recover_inflight()` 转为 `UNKNOWN`。
- 先在临时目录、fake allowlisted group 和 no-send 环境演练；synthetic/bootstrap/fake evidence 不计入生产门槛。

## 1. 暂停与封存

通过控制面为每个目标群提交 `PauseRuntime(paused=True)`，包含管理员、原因和 Expected Version。确认 Governance audit 已持久化暂停后，停止或重载唯一插件实例，并确认没有其他实例写同一数据目录：

```text
data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db
```

记录 UTC 时间、部署版本、管理员、受影响群、源路径和原因。不得手工改 Inbox、Cursor、Journal、Snapshot、Outbox 或 Projection。

## 2. 一致备份

目标必须是不存在的新文件：

```python
from pathlib import Path
from groupmate.social_runtime.recovery import backup_v2_database

backup_v2_database(
    Path("data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db"),
    Path("/absolute/backup/groupmate-social-runtime-v2.db"),
)
```

该函数在备份前校验源 V2 schema/integrity，使用 SQLite backup API 纳入 WAL 一致状态，并再次校验目标。任何异常都视为失败，不得删除或覆盖源库。

## 3. 临时恢复与核对

将备份恢复到隔离临时目录并逐项记录：

1. `PRAGMA integrity_check` 为 `ok`，schema version 受支持。
2. 每个 Actor 的 Cursor、最新 Snapshot 和 committed event replay 范围一致。
3. Journal `effect_id` 唯一，数量及 correlation 因果链与备份一致。
4. Outbox 状态计数一致；`SENT` 回执/bot ledger 和 `UNKNOWN` 均保留且不进入 `claim_ready()`。
5. `SENDING` 仅转为 `UNKNOWN`，禁止猜测成功或重置为 `READY`。

聚焦离线演练：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/recovery/test_disaster_recovery.py \
  tests/recovery/test_projection_rebuild.py \
  tests/recovery/test_delivery_recovery.py
```

## 4. Projection 重连与重建

先按原 projection name 创建 `ProjectionConsumer` 并继续 `consume()`，确认保存的 Cursor 不会重复应用 Journal。损坏时只重建受影响 read model：

```python
from pathlib import Path
from groupmate.social_runtime.control.projections import ProjectionConsumer

path = Path("/absolute/restore/groupmate-social-runtime-v2.db")
ProjectionConsumer(path, "activity").rebuild("activity")
```

核对 Projection cursor、item 数量和查询。Projection 恢复不得阻塞或回滚 Actor、Task、Journal 或 Outbox。

## 5. 恢复 fake allowlisted group

只在隔离环境将一个 fake group 加入 `enabled_groups` 与 `social_runtime_test_groups`，以 `SHADOW`/no-send 解除暂停。确认 replay/Snapshot/Journal 一致、Projection 可重建、Dispatcher 对 `SENT`/`UNKNOWN` 无发送且 transport 调用为零，并确认缺少 installed-live evidence 时 `ReadinessGate` 仍失败。

该结果只能记为 `PASS_OFFLINE`。真实部署恢复仍要求 installed-live SHADOW、冻结 holdout、连续 24h 观察、页面/容量证据、旧实例停止确认及 supervised/canary/allowlist 放量。

缺少任一证据时必须标记 `BLOCKED_NOT_COLLECTED`，保持 `OFF`/`SHADOW` 与暂停，不启用真实 QQ、不发布 release、不自动启用旧版本，也不手改 Outbox。
