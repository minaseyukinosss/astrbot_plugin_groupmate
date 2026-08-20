# Social Runtime v2 容量与背压运行手册

## 当前证据边界

Task 4 使用确定性虚拟时间负载验证调度、容量记账和公开预算，不把它解释为操作系统耐久 soak。固定 workload 是 50 群、每群 5 message/s、1,800 秒，共 450,000 个事件，另有 10 个相互重叠的长任务。运行器不会 wall-clock sleep，也不会连接真实 Provider、OneBot 或 QQ；`SHADOW` 仍然禁止创建发送副作用。

运行命令：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/recovery/test_production_fault_matrix.py \
  tests/evaluation/test_load_budget.py
```

Python 调用 `eval.load_runner.run_fake_load()` 返回 `LoadReport`。`to_json()` 使用排序 key、紧凑分隔符和有限数值，因此相同输入逐字节相同。每个预算都公开 `observed`、`budget`、`applicable` 和 `pass`；不适用项的 `pass` 是 `null`，不能伪装为通过。

## 固定 workload 形状

- 每个虚拟秒为每群逐项记入 5 个事件；streaming accumulator 实际遍历精确 450,000 个输入而不物化事件对象。默认 `ingested == committed == 450000`、`dropped == 0`；fault profile 会从同一状态导出 drop 和 backlog verdict，不能用固定常量替代。
- 每群每 30 秒有一个确定性的 FAST 直接事件，共 3,000 个 FAST denominator；群号错峰，避免用单批常量伪造延迟。
- 持续 Ambient 流量按 5 秒有界窗口聚合，共生成 18,000 个 Ambient decision。超过 frame deadline 后 8 秒仍不能完成的机会会 fail-closed 丢弃；accepted latency denominator 和 expired count 分开公开，总 expiry denominator 固定为 18,000。
- 10 个长任务通过真实 `TaskRuntime` 执行 `PROPOSED → QUEUED → RUNNING`，在独立 SQLite 状态中产生 30 个 Task events；报告公开 proposed/running/peak concurrency，默认峰值为 10。
- 虚拟 decision 调度使用生产 `WorkerAdmissionQueue`；另一个有界 probe 通过真实 `CognitionService` concurrency gate 跨群提交工作。FAST 排在已经等待的 AMBIENT 之前，但不会中断已运行工作；报告公开 gate submitted/peak/priority evidence、`direct_admitted_ahead_of_ambient` 和 `direct_starvation_count`。
- Projection lag 由逐秒 Projection consumer backlog 状态计算；fault profile 可以降低 consumer capacity，使同一状态推导出的 lag budget 确定性失败。

## 公开预算

| 名称 | 初始预算 | denominator / 语义 |
| --- | ---: | --- |
| Actor backlog | 告警 100 / 单群 | 未关闭 Ambient 窗口中的事件峰值 |
| Worker concurrency | 配置硬限制，默认 12 | 跨群共享的实际运行 Worker 峰值 |
| Worker cost | 50 cost units / virtual second | 实际 admitted FAST=1、Ambient=2 的总成本除以 1,800；已过期未运行的 Ambient 不计成本 |
| Fast decision P95 | ≤ 2,500 ms | 3,000 个 FAST decision；不包含外部 Task duration |
| Ambient decision P95 | ≤ 8,000 ms | accepted Ambient decisions |
| Ambient decision P99 | ≤ 8,000 ms | accepted Ambient decisions；避免 P95 隐藏尾部 |
| Ambient expired rate | 0 | `expired / 18,000 generated Ambient decisions`，任何过期均 fail-closed 并阻止放量 |
| Projection lag P95 | ≤ 5,000 ms | 21,000 个 decision projection |
| Unknown delivery rate | < 0.001 | `UNKNOWN parts / attempted parts` |

Latency 的 P50/P95/P99 使用固定 nearest-rank ceiling：先升序排列 N 个样本，P 百分位取一基位置 `ceil(N × P / 100)`。各 latency 和 delivery rate 使用独立 denominator，绝不混合。

默认 hard cap 12 时 18,000 个 Ambient 全部按期完成。诊断 profile `worker_concurrency_limit=1` 会稳定产生 16,498 个 expiry：accepted Ambient P99 仍为 7,794 ms，但 `ambient_expired_rate=0.916555555556` 明确失败，证明 P95/P99 不会掩盖被丢弃尾部。

当前 no-send fake load 的 attempted delivery parts 为零，所以 unknown delivery 明确报告 `applicable=false, pass=null`。它不是零风险证明；进入生产接管前必须由 Task 5/6 提供含 delivery traffic 的独立证据。

## 配置与处置

`worker_concurrency_limit` 是 AstrBot 配置中可见的正整数，默认 12。它由单个 Social Runtime Manager 的 CognitionService 跨所有启用群共享执行，不是每群限制。减小它可以硬控并发，但可能抬高 FAST/Ambient latency；修改后必须重跑 load report，并保存完整 JSON。

出现任一 `applicable=true, pass=false` 时保持 `SHADOW/OFF`：

1. Actor backlog 超限：先检查 Ambient 聚合、输入尖峰和 DB busy，不通过丢事件降压。
2. Worker concurrency 超限：视为 hard-cap 实现故障，停止发布；不能只提高文档预算。
3. Worker cost 或 latency 超限：降低 worker 选择/成本或增加经验证的容量，再重跑；Fast 不得排在 Ambient backlog 后。
4. Ambient P99/expiry 超限：保持 SHADOW；runtime 在 deadline guardrail 处把 work request 持久化为 `explicit_discard/attention_deadline_expired`，不得对过期 scene 再调用 cognition 或创建 capture。
5. Projection lag 超限：修复或重建 Projection。重建必须同时重放 SSE event view；Actor、Task 和 Outbox 主链必须继续，不能为追 Projection 回滚事实。
6. Unknown delivery 出现：保持 `UNKNOWN` 并人工核查平台证据，绝不盲重试。

Task 2 的 `EvaluationReport` 只向容量报告原样提供 latency、cost 与 safety issue count facts；Task 4 不读取或合并 lane quality 指标。Task 3 的 pending `shadow_capture_evidence` 继续由 runtime 原子写入并由 bridge 幂等确认，容量与故障工具不得建立第二套捕获链路。
