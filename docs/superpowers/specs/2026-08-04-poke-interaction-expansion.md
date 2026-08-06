# Poke Interaction Expansion

日期：2026-08-04

状态：已实现

上位设计：`2026-08-03-host-event-adapter-phase-b-design.md`

## 1. 目标

在现有 `HOST_INTERACTION` 主链路上扩展戳一戳：

1. **克制**：冷却 / 偶发沉默 / 暴戳压力话术 / 历史文案
2. **回戳出站**：可选 `OutboundKind.POKE`
3. **跟风戳**：他人互戳时按概率跟一脚（仅群聊，不主动戳说话的人）

只吸收拟人「克制 + 微反应 + 轻度凑热闹」，不引入独立回复池或主动戳系统。

## 2. 不变量

- 最终表达只走 Persona / SpeakContract / OutputFirewall / Composer / Delivery / Outbox
- 合成互动不写长期人物记忆、不授续聊；默认不 `stop_event()`（`poke_exclusive` 可开）
- 行为策略默认代码内置；WebUI 保留开关 + 可选高级覆盖（概率/冷却/独占/轻量 Face）
- **不做主动戳**（不因普通群消息去戳人）
- 不独立 LLM 旁路、不巨型回复池

## 3. 产品语义

| 模式 | 定义 |
|------|------|
| 被戳回应 | 别人戳 Bot → 默认应回应，允许策略沉默 |
| 暴戳 | 同人短窗内反复戳 Bot → 压力升级，语气更冲或划界 |
| 跟风戳 | A 戳 B（B 非 Bot）→ 按概率戳受害者或发起者，默认偏文字少、动作多 |

沉默是参与决策结果，不是 Adapter 丢弃。历史：`{speaker} 戳了戳 {character_name}`；跟风可见 `{A} 戳了戳 {B}`。
Bot 自己的回戳 / 跟风戳必须以短期可读痕迹进入 session / bot_delivery 文案（如 `戳了戳 {target}`），以便用户追问「你戳我干什么」时能承认动作；仍不写长期人物记忆、不授续聊。

## 4. InteractionPolicy（代码内置）

| 字段 | 默认 | 说明 |
|------|------|------|
| `poke_react_probability` | `0.88` | 被戳反应概率；未命中 `poke_skip` |
| `poke_cooldown_seconds` | `8` | 同人被戳冷却 |
| `poke_session_per_minute` | `6` | 同群每分钟被戳反应上限；`0` 不限制 |
| `poke_back_probability` | `0.35` | 被戳且允许回戳时，含戳动作的概率 |
| `poke_only_share` | `0.28` | 含戳时改为「只戳不说话」的份额 |
| `poke_burst_probability` | `0.18` | 友好/亲近且压力正常时双戳概率 |
| `poke_burst_max` | `2` | 双戳次数上限 |
| `poke_interval_seconds` | `0.45` | 连续出站戳间隔（防风控） |
| `poke_bystander_probability` | `0.33` | 跟风基础概率（再按好感缩放） |
| `poke_bystander_cooldown_seconds` | `20` | 同群跟风冷却 |
| `poke_bystander_target` | `victim` | `victim` / `poker` / `random` |

Composer 被戳三态：只短句 / 戳+短句 / 只戳。跟风默认纯戳；友好/亲近可偶发双戳。跟风概率缩放：警惕×0.55、中性×1、友好×1.25、亲近×1.4（上限 0.85）。

暴戳复用并特化 `direct_pressure_*`（HOST_INTERACTION 计入）；NUDGE/PESTER 改嫌弃话术，AFTER_BOUNDARY / 敌对 → `BOUNDARY` 或沉默。

## 5. 事件适配

`PokeEventAdapter`（需 `poke_enabled`）：

- 目标为 Bot → `interaction_kind=poke`，`poke_role=direct`
- 目标为他人 → `interaction_kind=poke`，`poke_role=bystander`，metadata 带 `poker_id` / `target_id`
- 字段不全 / 非 aiocqhttp → bypass

跟风与被戳共用 `HOST_INTERACTION`；`poke_role` 决定参与与 Composer 策略。

## 6. 参与决策

**direct：**

1. 查 `PokeThrottle` → 冷却/跳过则 silence（**不**计入暴戳压力）
2. 允许后才 `observe` 压力；Act：NORMAL→`PLAYFUL_REPLY`；高压力友好→嫌弃 playful；敌对/AFTER_BOUNDARY→`BOUNDARY`
3. **成功出站后**才 `mark_direct_reacted`

**bystander：**

1. 查跟风冷却 / 概率；不命中则 silence（`poke_bystander_skip` / `poke_bystander_cooldown`）
2. 义务 `OPEN_OPTIONAL`（受开放发送预算约束）
3. 默认倾向产出 POKE 段（需 `poke_back_enabled`）；未开回戳则短 playful 一句或沉默
4. **成功出站后**才 `mark_bystander_reacted`；短期痕迹优先群名片/昵称
5. 不授续聊、不写人物记忆

## 7. Phase 2 出站

- `OutboundKind.POKE` + `target_user_id`
- `poke_back_enabled` 默认 `false`：任何出站戳（回戳与跟风）都要求开启
- PlatformPort：OneBot/NapCat `group_poke` / `send_poke`；失败保留文本段
- Composer：direct 可「只戳 / 戳+短句 / 只短句」；bystander 优先单戳

## 8. WebUI 高级项（可选覆盖）

| 配置 | 默认 | 说明 |
|------|------|------|
| `poke_exclusive` | `false` | 接管后 `stop_event`，防与其他戳插件双响 |
| `poke_face_enabled` | `false` | 被戳偶发系统 Face（非表情包图库） |
| `poke_react_probability` 等 | 见 InteractionPolicy | 非法值回退代码默认 |

## 9. 明确延后

表情包关键词池、自定义「拍了拍」文案、**主动戳**（群消息触发去戳人）。

## 10. 验收

- 冷却内第二次被戳 → silence
- 压力升级改变 act / fallback
- 历史含「某人 戳了戳 爱弥斯」及他人互戳可读行
- `poke_back_enabled=false` 永不发出 POKE
- 跟风：互戳事件可触发，有冷却与概率；目标策略可测
- `poke_exclusive=true` 时对已接管戳事件调用 `stop_event`
- 既有 adapter / ownership 回归通过
