# Grounded Dialogue Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans，在当前会话逐项实施；用户要求当前目录、不提交 git。

**Goal:** 让模型看到可验证的双向对话，并把批准的回复锚点传到生成。

**Architecture:** 复用冻结事件与 Outbox，增加只读上下文组装器。历史证据与行动证据分开；场景诊断独立于投递状态。

**Tech Stack:** 现有 Python dataclass、SQLite、pytest、直连 DeepSeek，无新依赖。

**Spec:** docs/superpowers/specs/2026-09-04-grounded-dialogue-design.md

## Global Constraints

- 不提交 git，在当前目录保留所有既有改动。
- 不修改 AMBIENT 阈值、TTL、租约、SHADOW 发送门、冷启动、媒体或 DeepSeek 接入方式。
- 新 JSON 字段提供默认值；不建表、不增加模型调用。

## Task 1: 场景失败诊断与契约

Files: social_scenes.py、adapters/social_scene_model.py、manager.py、adapters/astrbot_bridge.py、control/message_traces.py；测试 test_social_scenes.py、test_chat_mainline.py。

接口：SceneInterpretationResult.diagnostic: Mapping | None；ShadowEvaluation.scene_diagnostic: Mapping | None；trace.decision.scene_diagnostic。

- [x] 在现有场景测试中注入错误 target_scope，断言 `result.diagnostic['code'] == 'scene_model_invalid'`，并在桥接场景断言失败信息仍与 SENT 共存。
- [x] 运行对应测试，确认缺少诊断导致失败。
- [x] 为失败分类加入有界结构信息，保留原 fallback 语义；SceneJsonModel 给出已有枚举和类型契约。
- [x] 重跑场景与桥接测试，确认没有把可发的回退变成沉默。

## Task 2: 冻结、回执约束的对话上下文

Files: 新建 social_runtime/dialogue.py；manager.py、cognition/contracts.py、cognition/ambient_worker.py、adapters/deepseek_cognition.py、social_context.py、replying.py。

接口：`DialogueContextReader(path).read(persona_id, group_id, events, focus_event_ids) -> tuple[SocialEventEnvelope, ...]`；`CognitiveContext.context_events=()`；参与 proposition 的 `context_evidence_event_ids=()`。

- [x] 新增一个回执上下文真实 SQLite 用例，断言只读取 SENT 文本、同群隔离、回显去重和源库无新事件。
- [x] 扩展现有 DirectAmbientWorker 用例：模型引用历史 Bot ID 作为 context evidence 可以通过，但当作 anchor 必须拒绝。
- [x] 运行确认失败后实现 Reader、优先级预算和冻结上下文传递；保留 focus 与 Blackboard 边界。
- [x] 普通/知识生成都使用同一事实字段，保留时间/引用/Bot 标识。
- [x] 跑相关 contracts 与上下文测试。

## Task 3: 选中的锚点贯通

Files: intentions.py、manager.py、adapters/astrbot_bridge.py、replying.py；现有 test_chat_mainline.py。

接口：`CandidateIntention.anchor_event_id: str | None = None`；`ShadowEvaluation.resolve_reply_source() -> SocialEventEnvelope | None`。

- [x] 构造 A 提问、B 随后说话的窗口，模型选中 A；断言 scene.source_event_id、scene.target_id、plan.target_id 和生成消息锚点均属于 A。
- [x] 确认旧行为失败后实现锚点传播，校验 focus/作者/非命令/非自身；原 trace 身份不变。
- [x] 验证旧 capture/candidate 无新字段可恢复，FAST/CONTINUATION 与 chorus 保持可用。

## Task 4: 验证和交付

- [x] `.venv/bin/python -m pytest tests/social_runtime tests/scenarios tests/shared -q --import-mode=importlib`
- [x] 跑受影响 contracts；`git diff --check`。
- [x] 只读生产库重建三角洲输入，确认是否有前情、锚点仍为当前消息，并报告缺失范围。
- [x] 给出实际测试结果及未做的真实模型/线上验证，不声称已达到目标效果。

Baseline: 用户指定三组测试 808 passed（2026-09-04，本轮修改前）。

## 验证记录与剩余验证

- 指定回归：814 passed（20.03s）；相关契约：70 passed（1.92s）；git diff --check 通过。
- 独立只读审查发现并已复核修正：场景二次预算挤掉 Bot 前情、原始触发消息覆盖冻结引用、历史扩充挤掉复读链证据。场景直接复用冻结内容，原 chorus 最近 20 条检测范围保持不变，必要链证据保留但不加入行动 focus。
- 额外检查 test_message_trace_bridge 时遇到 runtime_status 的 knowledge_status 期望键不一致；该状态接口和期望均不是本轮修改点，未顺手改动。
- 生产只读回放：我在玩三角洲 → 5 条近期消息；你在三角洲吗 → 6 条。两者都包含 2 条有回执的 Bot 发言，只有当前成员消息可行动。
- 读取历史按目标分析已有的 300 秒静默间隔切分，明确引用保留；这是上下文分段，不改变租约/TTL。
- 场景 JSON 契约与诊断已修补，但尚未取得新版本真实 DeepSeek 返回；不能宣称线上 15/15 回退已消失，也没有测得真实回复召回率增益。
