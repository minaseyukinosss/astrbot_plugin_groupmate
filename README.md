# AstrBot Groupmate Social Runtime v2

Groupmate 正在以 clean-slate 方式重建为持续存在的群聊社会智能体。新主线以 Durable Event Fabric、PersonaSupervisor、GroupSceneActor、Social Governor、ActionPlan、Task Runtime 和 Transactional Outbox 为核心，不兼容旧插件的内部架构、配置和数据库。

当前开发阶段：Phase A / Clean-slate 基础运行时。

- 当前允许模式：`OFF`、`SHADOW`
- 新数据库：`groupmate-social-runtime-v2.db`
- 旧 `groupmate.db`：不会读取或迁移
- 权威规格：`docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`
- 实施路线图：`docs/superpowers/plans/2026-08-18-social-runtime-v2-roadmap.md`

在 Phase A 运行时完成前，Bridge 会 fail closed，不会发送群消息或执行外部副作用。

## 开发验证

```bash
/Users/minase/Desktop/ams/astrbot_plugin_groupmate/.venv/bin/python -m pytest -q -p no:cacheprovider
```
