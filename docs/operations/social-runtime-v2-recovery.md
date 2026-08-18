# Social Runtime v2 恢复手册

本文只适用于 `groupmate-social-runtime-v2.db`。不要读取、复制、迁移或修改旧 `groupmate.db`。

## 1. 停止新事件进入

先将插件 `runtime_mode` 改为 `OFF`，再停止或重载插件实例。确认没有第二个 AstrBot 实例仍在使用同一数据目录。恢复期间不要手工把 Inbox 标成 `committed`，也不要直接修改 Cursor。

## 2. 生成一致备份

数据库开启 WAL，优先使用 SQLite 在线备份，而不是只复制主文件：

```bash
sqlite3 groupmate-social-runtime-v2.db ".backup 'groupmate-social-runtime-v2.recovery.db'"
sqlite3 groupmate-social-runtime-v2.recovery.db "PRAGMA integrity_check;"
```

输出必须为 `ok`。保留原数据库、备份数据库及操作时间；所有演练先针对备份。

## 3. 检查 Inbox、Cursor 与 Outbox

```bash
sqlite3 groupmate-social-runtime-v2.recovery.db \
  "SELECT status, COUNT(*) FROM inbox GROUP BY status ORDER BY status;"
sqlite3 groupmate-social-runtime-v2.recovery.db \
  "SELECT actor_key, last_sequence, version FROM actor_cursors ORDER BY actor_key;"
sqlite3 groupmate-social-runtime-v2.recovery.db \
  "SELECT status, COUNT(*) FROM outbox GROUP BY status ORDER BY status;"
```

`pending`、`failed` 或同一 Actor 所有的 `processing` 事件可以由 V2 重领。Shadow 模式的 Outbox 必须为空；如果不为空，停止恢复并调查，禁止尝试发送。

## 4. 在备份上 dry replay

用 `SHADOW`、相同 `enabled_groups` 和备份数据库启动一次 Runtime，然后执行 `manager.start()` 与 `manager.drain()`。Shadow 的 `NoSideEffectExecutionPort` 会拒绝任何外部动作。重放完成后记录：

- 各 Actor 的 Cursor；
- Inbox 状态计数；
- Journal 行数及 `effect_id` 去重结果；
- 最新 Persona/Group Snapshot 的 SHA-256；
- Outbox 行数，必须仍为零。

可以用下面的只读命令检查 Snapshot 内容哈希：

```bash
sqlite3 groupmate-social-runtime-v2.recovery.db \
  "SELECT actor_key, version, hex(sha3(payload_json,256)) FROM snapshots ORDER BY actor_key,version;"
```

若本机 SQLite 未编译 `sha3`，导出 `actor_key/version/payload_json` 后使用系统 SHA-256 工具计算。对同一备份重复演练时，最终 Cursor、Journal 数量和 Snapshot 哈希必须一致。

## 5. 恢复正式消费

确认演练一致后，备份生产 V2 数据库，将插件模式恢复为 `SHADOW` 并启动单一实例。Manager 启动时会扫描允许群中的 `pending/failed/processing` Inbox，恢复相应 Group Actor，并从最近 Snapshot 与 Cursor 自动继续消费。

恢复后再次检查 Inbox、Cursor、Journal 和 Snapshot。重复事件必须沿用原 `event_id/effect_id`，不能产生第二次影响。

## 6. 恢复验收

满足以下全部条件才算完成：

- `PRAGMA integrity_check` 为 `ok`；
- 允许群的可恢复 Inbox 已消费，失败项有明确错误原因；
- Cursor 单调前进且未越过未提交事件；
- Journal 中没有重复 `effect_id`；
- Persona 与 Group Snapshot 可重建且哈希稳定；
- `outbox` 为空；
- Execution 调用为零；
- 未触碰旧数据库。

任何一项不满足都应重新切回 `OFF`，保留现场和备份，不要通过手工改状态绕过恢复协议。
