# Social Runtime v2 发布候选验收记录

## 当前结论

**BLOCKED — 所有生产证据均为 `BLOCKED_NOT_COLLECTED`。这不是生产验收通过或发布授权。**

仓库内可执行门禁单独记为 `PASS_OFFLINE`。它们不能与真实部署门槛汇总成绿色结论，synthetic/bootstrap/fake evidence 也不能替代 installed-live evidence。

## 标识

- 候选版本：`1.0.0-rc.3`
- 实施基线 commit：`8643f1edb5ade6c6961fcc476915a59e6975669f`
- 环境：本地隔离 worktree，Python 3.13；未连接 AstrBot/QQ
- 数据：仅临时 V2 SQLite 数据库和 fake group

## PASS_OFFLINE

| UTC 时间 | 命令 | 结果 | 范围 |
| --- | --- | --- | --- |
| 2026-08-20T08:21Z | `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/.venv/bin/python -m pytest -p no:cacheprovider -q tests/recovery/test_disaster_recovery.py tests/social_runtime/test_readiness.py tests/recovery/test_no_dual_sender.py` | `PASS_OFFLINE` — 12 passed in 0.50s | pause、backup/restore、replay、Snapshot/Journal/Outbox、Projection rebuild、SENT/UNKNOWN 不重发，以及 Task 5 fail-closed readiness/no-dual |
| 2026-08-20T08:22Z | `/Users/minase/Desktop/ams/astrbot_plugin_groupmate/.venv/bin/python -m tests.architecture_guard` | `PASS_OFFLINE` — exit 0 | clean-slate 架构边界 |
| 2026-08-20T08:22Z | `git diff --check` | `PASS_OFFLINE` — exit 0，无输出 | patch whitespace |

`PASS_OFFLINE` 只说明所列命令在候选树成功，不代表真实效果、真实平台交付、长期运行或生产恢复通过。

## BLOCKED_NOT_COLLECTED

| 必需生产证据 | 状态 | 解锁条件 |
| --- | --- | --- |
| AstrBot 实际安装的 no-send SHADOW | `BLOCKED_NOT_COLLECTED` | 在目标部署采集真实事件与候选决策 |
| 至少 100 条人工复核、冻结 calibration/holdout 与场景覆盖 | `BLOCKED_NOT_COLLECTED` | 按版本冻结并由管理员批准；bootstrap 不计入 |
| 连续 24h installed-live SHADOW 与真实容量/安全指标 | `BLOCKED_NOT_COLLECTED` | 完整观察窗通过且无 UNKNOWN/safety/dual-sender |
| 旧实例停止及无进行中外部副作用确认 | `BLOCKED_NOT_COLLECTED` | 部署管理员提供作用域/版本绑定确认 |
| 2h supervised send | `BLOCKED_NOT_COLLECTED` | readiness 全通过后只在首个测试群执行 |
| 24h canary 与 1→3→10→all 分档观察 | `BLOCKED_NOT_COLLECTED` | 每档满足规定时长并重新审核报告 |
| 真实 QQ/platform delivery 与生产 DR | `BLOCKED_NOT_COLLECTED` | 授权窗口验证真实回执、UNKNOWN 处置和无重复发送 |

因此本记录不批准 `SOCIAL_RUNTIME` 生产接管、不完成 Phase E、不发布 release，也不触发 branch finishing。
