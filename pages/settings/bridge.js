const POLL_INTERVAL_MS = 15_000;

export class ApiBridge {
  constructor(pageBridge = window.AstrBotPluginPage) {
    if (!pageBridge) throw new Error("AstrBot Plugin Page bridge is unavailable");
    this.bridge = pageBridge;
    this.subscriptionId = null;
    this.pollTimer = null;
  }

  ready() {
    return this.bridge.ready();
  }

  query(endpoint, params = {}) {
    return this.bridge.apiGet(endpoint, params);
  }

  command(body) {
    return this.bridge.apiPost("commands", body);
  }

  async connect({ params, onEvent, onState, onPoll }) {
    await this.disconnect();
    onState({ state: "connecting", impact: "正在建立实时连接" });
    try {
      this.subscriptionId = await this.bridge.subscribeSSE(
        "events",
        {
          onOpen: () => {
            this.stopPolling();
            onState({ state: "connected", impact: "实时数据已连接" });
          },
          onMessage: (event) => {
            if (event.parsed && typeof event.parsed === "object") {
              if (event.parsed.kind === "snapshot_required") {
                onPoll(params);
                return;
              }
              onEvent(event.parsed);
            }
          },
          onError: () => {
            onState({
              state: "disconnected",
              impact: "实时连接中断，已切换到最多延迟 15 秒的 polling",
            });
            this.startPolling(params, onPoll, onState);
          },
        },
        params,
      );
    } catch (error) {
      onState({
        state: "polling",
        impact: `SSE 不可用：${error.message || "未知错误"}；最多延迟 15 秒`,
      });
      this.startPolling(params, onPoll, onState);
    }
  }

  startPolling(params, onPoll, onState) {
    if (this.pollTimer) return;
    onState({ state: "polling", impact: "实时更新降级，最多延迟 15 秒" });
    onPoll(params);
    this.pollTimer = setInterval(() => onPoll(params), POLL_INTERVAL_MS);
  }

  stopPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  async disconnect() {
    this.stopPolling();
    if (this.subscriptionId) {
      await this.bridge.unsubscribeSSE(this.subscriptionId);
      this.subscriptionId = null;
    }
  }

  static describeError(error) {
    const message = String(error?.message || error || "请求失败");
    if (message.includes("409")) {
      return { status: 409, code: "conflict", impact: "版本冲突，请刷新后重试" };
    }
    if (message.includes("403")) {
      return { status: 403, code: "forbidden", impact: "当前管理员无权执行此操作" };
    }
    if (message.includes("400")) {
      return { status: 400, code: "invalid", impact: "输入未通过服务端校验" };
    }
    return { status: 500, code: "failed", impact: message };
  }
}
