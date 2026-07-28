# 爱弥斯行为人格与好感机制实施计划

> **执行要求：** 代码实施时必须使用 `superpowers:executing-plans`（按计划执行）和 `superpowers:test-driven-development`（测试驱动开发）；完成前必须使用 `superpowers:verification-before-completion`（完成前验证）。

**目标：** 在不改变爱弥斯身份的前提下，实现结构化行为人格和五档好感机制，删除会绕过身份或造成关键词误判的旧结构，并为下一阶段 `ParticipationDecisionEngine`（统一参与决策引擎）提供不可变的人格与好感参与策略。

**架构：** `RelationshipState`（关系状态）继续作为唯一关系持久化状态；新 `AffinityBand`（好感档位）从其中的好感值派生。`PersonaParticipationProfile`（人格参与档案）定义不同好感档位允许成立的参与动机，本阶段先让生成姿态读取档位，下一阶段的新决策引擎读取同一策略。删除旧三档好感、关键词情绪、关键词社会事件分类、外部人格覆盖和可变角色名入口。

**技术栈：** Python 3.7+、冻结 dataclass（不可变数据类）、Enum（枚举）、pytest、SQLite、AstrBot 插件配置与 Pages。

**设计依据：** `docs/superpowers/specs/2026-07-28-aemeath-behavior-persona-alignment-design.md`

---

## 实施纪律

- 每个任务先新增或修改测试，并实际观察预期失败，再写实现；
- 手工编辑全部使用 `apply_patch`；
- 不上传、不提交目标聊天原文、目标账号或目标媒体；
- 不把目标数据占比变成运行时随机率；
- 不把好感连续值重新接入旧 `OpportunityArbiter`（机会仲裁器）的效用公式；
- 不自动删除已部署数据库的旧 `favorability`（旧好感度）表；
- 每个任务独立提交，便于定位回归；
- 不推送远端。

## Task 1：建立五档好感领域模型

**文件：**

- 新建：`groupmate/social/affinity.py`
- 修改：`groupmate/social/projector.py`
- 修改：`groupmate/social/__init__.py`
- 新建：`tests/test_affinity.py`
- 修改：`tests/test_social_events.py`

### Step 1：先写好感档位边界测试

在 `tests/test_affinity.py` 中覆盖：

- `band_for_affinity`（好感值转档位）在 -100、-50、-49、-1、0、29、30、69、70、100 的边界；
- `clamp_affinity`（好感值限幅）把越界值限制到 -100 至 100；
- `initial_affinity_for_relationship`（配置关系初始好感）返回普通群友 0、闺蜜 50、最亲近 80；
- `snapshot_for_relationship`（关系状态转好感快照）不暴露数值；
- 高 `boundary_pressure`（边界压力）优先于亲近好感，返回坚定边界姿态。

测试使用英文枚举和中文断言说明，例如：

```python
def test_affinity_band_boundaries():
    assert band_for_affinity(-50) is AffinityBand.HOSTILE
    assert band_for_affinity(-49) is AffinityBand.WARY
    assert band_for_affinity(0) is AffinityBand.NEUTRAL
    assert band_for_affinity(30) is AffinityBand.FRIENDLY
    assert band_for_affinity(70) is AffinityBand.CLOSE
```

### Step 2：运行测试并确认失败

运行：

```bash
pytest -q tests/test_affinity.py
```

预期：FAIL，`groupmate.social.affinity` 尚不存在。

### Step 3：实现好感档位与关系姿态

在 `groupmate/social/affinity.py` 中实现：

- `AffinityBand`（好感档位）：`HOSTILE`、`WARY`、`NEUTRAL`、`FRIENDLY`、`CLOSE`；
- `ResponsePosture`（回应姿态）：`FIRM`、`RESERVED`、`POLITE`、`WARM`、`CLOSE`；
- `AffinitySnapshot`（好感快照）：保存档位和回应姿态，不保存第二份持久化状态；
- `clamp_affinity`（限幅）；
- `band_for_affinity`（档位解析）；
- `initial_affinity_for_relationship`（配置关系初始值）；
- `snapshot_for_relationship`（关系状态转好感快照）。

`snapshot_for_relationship`（关系状态转快照）规则：

- 有 `RelationshipState`（关系状态）时使用其好感值；
- 无状态时使用配置关系初始值；
- 边界压力达到坚定边界条件时，回应姿态至少为 `FIRM`（坚定）；
- 返回对象不包含发言概率、连续效用权重或随机参数。

### Step 4：修正事件投影

修改 `SocialStateProjector`（关系状态投影器）：

- 从新好感模块导入限幅函数；
- `NEUTRAL`（普通互动）只增加熟悉度，不增加好感；
- `CORRECTION`（普通纠正）默认不降低好感；
- 已验证的感谢、帮助、友好玩笑可以小幅增加好感；
- 已验证的越界、骚扰明显降低好感并增加边界压力；
- 道歉只小幅修复，不能一次恢复原状态；
- 删除 `soft_trigger`（软触发）对普通事件好感变化的影响；
- 删除 `affinity_for_persona`（旧好感整数提取器）。

更新 `tests/test_social_events.py`：保留事件幂等、存储和重放测试；删除关键词分类器相关测试；新增普通互动不刷好感、道歉不立即清空边界压力的测试。

### Step 5：运行测试

运行：

```bash
pytest -q tests/test_affinity.py tests/test_social_events.py
```

预期：PASS。

### Step 6：提交

```bash
git add groupmate/social/affinity.py groupmate/social/projector.py groupmate/social/__init__.py tests/test_affinity.py tests/test_social_events.py
git commit -m "feat: add evidence-based affinity bands"
```

## Task 2：建立结构化爱弥斯行为人格档案

**文件：**

- 新建：`groupmate/persona/aemeath/behavior_profile.py`
- 修改：`groupmate/persona/aemeath/__init__.py`
- 修改：`groupmate/persona/aemeath/provider.py`
- 新建：`tests/test_behavior_profile.py`

### Step 1：先写不可变契约测试

测试以下公共类型：

- `ParticipationMotive`（人格参与动机）；
- `ParticipationInhibition`（人格参与抑制）；
- `AffinityParticipationRule`（好感参与规则）；
- `PersonaParticipationProfile`（人格参与档案）；
- `AEMEATH_PARTICIPATION_PROFILE`（爱弥斯默认参与档案）。

必须断言：

- 档案是冻结数据类，不能运行时修改；
- 不存在 `probability`、`weight`、`threshold`、`extroversion` 等字段；
- 五个 `AffinityBand`（好感档位）各有且仅有一条参与规则；
- `HOSTILE`（敌对）不开放关心、接梗和关系性偏好；
- `WARY`（警惕）只开放具体帮助、群体协调和既有话题归属；
- `FRIENDLY`（友好）与 `CLOSE`（亲近）才开放有证据的关心和互动延续；
- Provider（人格提供器）的 `participation_profile`（人格参与档案）属性只读返回默认档案。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_behavior_profile.py
```

预期：FAIL，新模块尚不存在。

### Step 3：实现不可变档案

使用冻结 dataclass（不可变数据类）和 Tuple（不可变元组）实现，避免可变字典泄漏。建议结构：

```python
@dataclass(frozen=True)
class AffinityParticipationRule:
    band: AffinityBand
    allowed_motives: Tuple[ParticipationMotive, ...]
    response_posture: ResponsePosture


@dataclass(frozen=True)
class PersonaParticipationProfile:
    identity_name: str
    motives: Tuple[ParticipationMotive, ...]
    inhibitions: Tuple[ParticipationInhibition, ...]
    affinity_rules: Tuple[AffinityParticipationRule, ...]
```

提供 `rule_for_affinity`（按好感档位读取规则）方法；缺档、重复档在构造时直接报错，失败关闭。

### Step 4：从爱弥斯人格提供器只读暴露

`AemeathPersonaProvider`（爱弥斯人格提供器）增加：

```python
@property
def participation_profile(self) -> PersonaParticipationProfile:
    return AEMEATH_PARTICIPATION_PROFILE
```

本任务不把档案接入旧机会效用公式。

### Step 5：运行测试并提交

```bash
pytest -q tests/test_behavior_profile.py tests/test_persona.py
git add groupmate/persona/aemeath/behavior_profile.py groupmate/persona/aemeath/__init__.py groupmate/persona/aemeath/provider.py tests/test_behavior_profile.py
git commit -m "feat: add aemeath participation profile"
```

## Task 3：用关系状态替换旧情绪与旧好感提示

**文件：**

- 修改：`groupmate/core/context_assembly.py`
- 修改：`groupmate/core/history_format.py`
- 修改：`groupmate/core/voice_anchor.py`
- 修改：`groupmate/core/__init__.py`
- 修改：`groupmate/persona/aemeath/provider.py`
- 删除：`groupmate/core/mood.py`
- 删除：`groupmate/persona/aemeath/moods.md`
- 修改：`tests/test_core_assembly.py`
- 修改：`tests/test_persona.py`
- 修改：`tests/test_affinity.py`

### Step 1：先改装配测试

测试要求：

- `DYNAMIC_BLOCK_ORDER`（动态块顺序）不再包含 `mood`（情绪）；
- `AssembledPrompt`（已装配提示）不再含 `mood_key`（情绪键）；
- `build_user`（构造用户上下文）接收 `relationship_state`（关系状态），不接收 `mood_key` 或 `favorability`（旧好感整数）；
- `<relationship_line>`（关系行）显示配置关系、离散好感档位和回应姿态，不显示 -100 至 100 数值；
- 多人指代不清时不注入任何人的亲密关系或好感状态；
- `VOICE_BEHAVIOR_NOTE`（旧口吻行为说明）不存在；
- 核心层仍不硬编码“爱弥斯”。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_core_assembly.py tests/test_affinity.py tests/test_persona.py
```

预期：FAIL，仍存在情绪块和旧好感参数。

### Step 3：删除情绪状态机

- 删除 `groupmate/core/mood.py`；
- 删除 `groupmate/persona/aemeath/moods.md`；
- 从装配器、Provider（人格提供器）和测试中移除所有 `mood_key`（情绪键）；
- 从 `DYNAMIC_BLOCK_ORDER`（动态块顺序）删除 `mood`；
- 从 `AssembledPrompt`（已装配提示）删除 `mood_key` 字段。

### Step 4：改造关系上下文

`format_relationship_line`（格式化关系行）改为接收完整 `RelationshipState`（关系状态）。内部调用 `snapshot_for_relationship`（关系状态转快照），输出自然中文姿态，例如“当前关系普通、态度中性有分寸”，但不得输出内部 ID、数值或枚举名。

`ContextAssembly`（上下文装配器）和 `AemeathPersonaProvider`（爱弥斯人格提供器）统一使用 `relationship_state` 参数。

### Step 5：清除核心层人格政策

删除 `VOICE_BEHAVIOR_NOTE`（旧口吻行为说明），`format_voice_anchor_block`（格式化口吻锚点）只包裹 Pack（人格提示词包）提供的文本。缺失锚点时使用角色无关的最小回退，不定义参与政策。

### Step 6：运行测试并提交

```bash
pytest -q tests/test_core_assembly.py tests/test_affinity.py tests/test_persona.py
git add groupmate/core/context_assembly.py groupmate/core/history_format.py groupmate/core/voice_anchor.py groupmate/core/__init__.py groupmate/persona/aemeath/provider.py groupmate/core/mood.py groupmate/persona/aemeath/moods.md tests/test_core_assembly.py tests/test_persona.py tests/test_affinity.py
git commit -m "refactor: replace mood prompts with relationship posture"
```

## Task 4：重写爱弥斯 Persona Pack（人格提示词包）

**文件：**

- 修改：`groupmate/persona/aemeath/persona.md`
- 修改：`groupmate/persona/aemeath/constraints.md`
- 修改：`groupmate/persona/aemeath/voice_anchor.md`
- 修改：`tests/test_persona.py`
- 修改：`tests/test_guardrails.py`
- 修改：`eval/rubrics/persona_judge.md`

### Step 1：先写人格语义测试

`tests/test_persona.py` 必须验证：

- 身份仍是《鸣潮》的爱弥斯，3.3 后恢复身体；
- 存在“默认观察、有具体贡献依据才参与、完成一个贡献后退出”；
- 人格能影响开放参与，但不能覆盖对话归属、安全、能力事实和明确回应义务；
- 包含敌对、警惕、中性、友好、亲近五档姿态；
- 负好感仍必须回应明确点名，但可以更冷、更短并拒绝非必要私人请求；
- 删除旧 `Favorability Logic`（早柚三档好感度）、“默认潜水”和“性格只管语气”；
- 不出现目标机器人的身份、固定口癖、泛化亲属称呼或花房设定；
- 只叫名字仍不自动问“怎么啦”；
- 有明确目的的澄清问题仍被允许。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_persona.py tests/test_guardrails.py
```

预期：FAIL，旧 Persona Pack（人格提示词包）仍含冲突规则。

### Step 3：重写三份人格文本

按设计文档落实：

- `persona.md`（完整人格）：身份核心、行为人格、五档好感姿态、关系分寸、场景参与和退出规则；
- `constraints.md`（框架约束）：明确对话归属和强制回应高于人格偏好，开放群体话题允许有独特贡献时参与；
- `voice_anchor.md`（末端人格锚点）：保持短小，只强调具体贡献、温暖独立和贡献后退出。

示例全部使用爱弥斯自身语境，不能复制目标聊天原句。

### Step 4：更新人工评分规则

`eval/rubrics/persona_judge.md` 增加：

- `relationship_posture`（关系姿态一致性）；
- 负好感时冷静但不辱骂；
- 正好感时温暖但不无依据亲密；
- 边界压力可以覆盖亲近语气。

### Step 5：运行测试并提交

```bash
pytest -q tests/test_persona.py tests/test_guardrails.py
git add groupmate/persona/aemeath/persona.md groupmate/persona/aemeath/constraints.md groupmate/persona/aemeath/voice_anchor.md tests/test_persona.py tests/test_guardrails.py eval/rubrics/persona_judge.md
git commit -m "feat: align aemeath behavior persona"
```

## Task 5：移除旧好感运行时与关键词社会事件自动写入

**文件：**

- 修改：`groupmate/engine/workflow.py`
- 修改：`groupmate/engine/opportunity.py`
- 修改：`groupmate/ports.py`
- 修改：`groupmate/memory/store.py`
- 修改：`groupmate/social/projector.py`
- 修改：`groupmate/social/__init__.py`
- 修改：`groupmate/models.py`
- 修改：`tests/fakes.py`
- 删除：`groupmate/core/favorability.py`
- 删除：`groupmate/social/events.py`
- 删除：`tests/test_favorability.py`
- 修改：`tests/test_social_events.py`
- 修改：`tests/test_opportunity.py`
- 修改：`tests/test_workflow.py`
- 修改：`tests/test_phase3_migrations.py`

### Step 1：先写运行时清理测试

覆盖：

- `OpportunityArbiter.evaluate`（机会仲裁评估）不再接受 `favorability`（旧好感整数）；
- 效用原因中不再出现 `rel=` 连续好感加成；
- `CognitiveWorkflow`（认知工作流）读取 `get_relationship_state`（读取关系状态），并把状态交给人格装配；
- 成功发送普通回复不会自动增加好感；
- 工作流不再构造关键词 `SocialEventClassifier`（社会事件分类器）；
- SQLite Store（SQLite 存储）保留社会事件和关系状态接口，但不再提供日常 `get/set/adjust_favorability`（读取、设置、调整旧好感）接口；
- 重建关系状态不再镜像写回旧表；
- 历史迁移仍能把旧表数值回填到 `relationship_state.affinity`（关系状态好感值）。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_opportunity.py tests/test_workflow.py tests/test_social_events.py tests/test_phase3_migrations.py
```

预期：FAIL，旧参数、旧分类器和旧表镜像仍存在。

### Step 3：改造工作流关系读取

在 `CognitiveWorkflow`（认知工作流）中：

- 删除 `_peek_favorability`（预读旧好感）；
- 删除 `_ensure_favorability`（确保旧好感）；
- 删除 `_record_social_success`（旧自动社会事件记录）；
- 删除 `social_classifier`（社会事件分类器）构造参数；
- 新增 `_relationship_state_for_target`（读取目标关系状态），只有唯一社会目标时读取；
- 把 `relationship_state`（关系状态）交给人格装配；
- 指代不清时传 `None`，不得猜测关系。

### Step 4：移除旧机会效用输入

从 `OpportunityArbiter`（机会仲裁器）删除 `favorability` 参数和 `relationship_relevance` 连续效用项。不要在这里接入新的好感档位；好感参与策略由下一阶段新统一决策引擎消费。

### Step 5：清理存储与端口

- 从 `MemoryRepository`（记忆仓储端口）、`SQLiteMemoryStore`（SQLite 记忆存储）和测试替身删除旧好感日常接口；
- `record_social_interaction`（记录社会互动）只处理明确传入的已验证事件，不读取或镜像旧表；
- `rebuild_relationship_state`（重建关系状态）只写 `relationship_state`；
- 保留历史迁移中的旧表创建与一次性回填，禁止新增删除表迁移；
- 删除 `core/favorability.py`（旧好感模块）、`social/events.py`（关键词社会事件分类器）和对应旧测试。

### Step 6：运行测试并提交

```bash
pytest -q tests/test_opportunity.py tests/test_workflow.py tests/test_social_events.py tests/test_phase3_migrations.py
git add groupmate/engine/workflow.py groupmate/engine/opportunity.py groupmate/ports.py groupmate/memory/store.py groupmate/social/projector.py groupmate/social/__init__.py groupmate/models.py tests/fakes.py groupmate/core/favorability.py groupmate/social/events.py tests/test_favorability.py tests/test_social_events.py tests/test_opportunity.py tests/test_workflow.py tests/test_phase3_migrations.py
git commit -m "refactor: retire legacy favorability runtime"
```

## Task 6：固定爱弥斯身份并删除人格覆盖旁路

**文件：**

- 修改：`groupmate/config.py`
- 修改：`groupmate/persona/aemeath/provider.py`
- 修改：`groupmate/core/context_assembly.py`
- 修改：`groupmate/host/llm.py`
- 修改：`groupmate/host/bridge.py`
- 修改：`groupmate/host/__init__.py`
- 修改：`groupmate/host/web_api.py`
- 修改：`_conf_schema.json`
- 修改：`pages/settings/index.html`
- 修改：`pages/settings/app.js`
- 修改：`README.md`
- 修改：`tests/test_config.py`
- 修改：`tests/test_core_assembly.py`
- 修改：`tests/test_plugin_loading.py`
- 修改：`tests/test_plugin_page_assets.py`

### Step 1：先写身份固定测试

必须验证：

- `PluginSettings`（插件设置）不再含 `persona_id`、`persona_prompt`、`character_name`；
- 即使旧配置仍带这些键，解析器也忽略它们，运行时身份仍是爱弥斯；
- `ContextAssembly`（上下文装配器）不存在 `identity_override`（身份覆盖）或 `set_identity_override`（设置身份覆盖）；
- `AemeathPersonaProvider`（爱弥斯人格提供器）不接受覆盖提示或可变角色名；
- `AstrBotPersonaProvider`（AstrBot 可覆盖人格提供器）不再导出；
- Web API 显示名来自固定 `CHARACTER_NAME`（角色名称常量），不来自配置；
- `aliases`（唤醒别名）、`group_brief`（群氛围）、关系表和字数护栏仍可配置。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_config.py tests/test_core_assembly.py tests/test_plugin_loading.py tests/test_plugin_page_assets.py
```

预期：FAIL，旧覆盖字段和 Provider（人格提供器）仍存在。

### Step 3：删除覆盖配置

- 从 `PluginSettings`（插件设置）和 `_conf_schema.json` 删除三个字段；
- 从 README 和设置页删除人格覆盖优先级说明；
- `persona_group`（人格配置组）只保留群氛围和输出护栏；
- 旧配置键静默忽略，不做身份兼容回退。

### Step 4：删除宿主覆盖 Provider

- 删除 `AstrBotPersonaProvider`（AstrBot 可覆盖人格提供器）；
- `AstrBotGenerationModel`（AstrBot 生成模型）继续依赖通用人格接口；
- Bridge（宿主桥接）直接构造 `AemeathPersonaProvider`（爱弥斯人格提供器）；
- 所有 Session（会话）、Delivery（发送）和 Projection（投影）使用固定 `CHARACTER_NAME`（角色名称常量）；
- Core（核心层）保留角色无关参数，但产品宿主不再从配置传入可变身份。

### Step 5：更新 Web 页面

- 状态 API 返回固定爱弥斯显示名；
- 删除外部人格 ID 和覆盖状态字段；
- 页面文案改为“爱弥斯行为与关系配置”；
- 不新增营销式说明或无关视觉改版。

### Step 6：运行测试并提交

```bash
pytest -q tests/test_config.py tests/test_core_assembly.py tests/test_plugin_loading.py tests/test_plugin_page_assets.py
git add groupmate/config.py groupmate/persona/aemeath/provider.py groupmate/core/context_assembly.py groupmate/host/llm.py groupmate/host/bridge.py groupmate/host/__init__.py groupmate/host/web_api.py _conf_schema.json pages/settings/index.html pages/settings/app.js README.md tests/test_config.py tests/test_core_assembly.py tests/test_plugin_loading.py tests/test_plugin_page_assets.py
git commit -m "refactor: fix runtime identity to aemeath"
```

## Task 7：删除旧社会回退开关并补全回归场景

**文件：**

- 修改：`groupmate/config.py`
- 修改：`groupmate/models.py`
- 修改：`groupmate/host/bridge.py`
- 修改：`_conf_schema.json`
- 修改：`README.md`
- 修改：`tests/test_config.py`
- 修改：`tests/test_social_events.py`
- 修改：`tests/test_behavior_profile.py`
- 修改：`tests/test_persona.py`

### Step 1：先写无回退测试

断言：

- `v3_social_enabled`（旧社会状态回退开关）不再出现在设置、策略、Schema（配置模式）或 README；
- 普通问答不产生好感增加；
- 首次模糊恋爱称呼不会自动产生负向事件；
- 只有显式构造的已验证越界事件才降低好感；
- 五档好感参与规则覆盖所有人格动机；
- 敌对档必须回应明确点名，但不开放关系性主动参与；
- 亲近档开放关心和接梗，但仍包含不垄断抑制规则。

### Step 2：运行测试并确认失败

```bash
pytest -q tests/test_config.py tests/test_social_events.py tests/test_behavior_profile.py tests/test_persona.py
```

### Step 3：删除开关与残留路径

删除配置字段、策略字段、Bridge（宿主桥接）传参、文档表格和旧测试。使用 `rg` 检查没有运行时残留：

```bash
rg -n "v3_social_enabled|SocialEventClassifier|delta_for_turn|favorability_early|relationship_relevance" groupmate _conf_schema.json README.md pages tests
```

预期：仅允许在历史迁移注释或明确的反向防回归断言中出现旧术语。

### Step 4：运行测试并提交

```bash
pytest -q tests/test_config.py tests/test_social_events.py tests/test_behavior_profile.py tests/test_persona.py
git add groupmate/config.py groupmate/models.py groupmate/host/bridge.py _conf_schema.json README.md tests/test_config.py tests/test_social_events.py tests/test_behavior_profile.py tests/test_persona.py
git commit -m "test: lock affinity and persona behavior contracts"
```

## Task 8：全量验证与隐私审计

**文件：**

- 按失败结果修正上述任务涉及文件；不得顺手重构无关模块。

### Step 1：运行定向测试

```bash
pytest -q tests/test_affinity.py tests/test_behavior_profile.py tests/test_persona.py tests/test_guardrails.py tests/test_core_assembly.py tests/test_social_events.py tests/test_opportunity.py tests/test_workflow.py tests/test_config.py tests/test_plugin_loading.py tests/test_phase3_migrations.py
```

预期：PASS。

### Step 2：运行全量测试

```bash
pytest -q
```

预期：全部通过；记录实际通过数量。

### Step 3：运行确定性评测

```bash
python3 -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/baseline.jsonl --output /tmp/groupmate-aemeath-affinity-baseline.json
python3 -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/phase2_behavior.jsonl --output /tmp/groupmate-aemeath-affinity-behavior.json
```

预期：两个命令退出码均为 0，错误数为 0。

### Step 4：检查旧结构残留

```bash
rg -n "默认潜水|性格只管语气|Favorability Logic|VOICE_BEHAVIOR_NOTE|SocialEventClassifier|persona_id|persona_prompt|v3_social_enabled" groupmate _conf_schema.json README.md pages tests
```

预期：运行时代码和用户配置中无命中；测试只允许反向断言。

### Step 5：检查人格身份与中文说明

```bash
rg -n "AffinityBand|PersonaParticipationProfile|RelationshipState|ParticipationMotive" groupmate docs/superpowers/specs docs/superpowers/plans tests
```

人工确认文档首次出现的类型名均搭配中文说明；运行时人格没有目标身份、目标口癖或目标关系称谓。

### Step 6：隐私审计

对本轮新增和修改内容检查：

```bash
git diff 8ccd01b..HEAD -- . ':!eval/results/**'
git status --short
```

确认：

- 没有聊天原文、目标账号、群号或本地导出路径；
- 没有把 `eval/results/` 本地结果加入提交；
- 没有自动删除旧数据库表；
- 没有未解释的工作区改动；
- 没有执行推送。

### Step 7：最终提交

只有存在验证阶段必要修正时才提交：

```bash
git add <本轮验证修正文件>
git commit -m "fix: close aemeath affinity regressions"
```

## 完成后的交付内容

最终向用户报告：

- 新五档好感及各档对参与和态度的影响；
- 已删除的旧人格、情绪、好感、关键词事件和覆盖入口；
- 旧数据库数据的迁移与保留方式；
- Persona Pack（人格提示词包）保持爱弥斯身份的证据；
- 定向测试、全量测试和两组确定性评测的实际结果；
- 当前提交范围和未推送状态；
- 下一阶段 `ParticipationDecisionEngine`（统一参与决策引擎）必须接入的 `affinity_policy`（好感参与策略）契约。
