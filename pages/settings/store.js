const INITIAL_CONNECTION = Object.freeze({
  state: "disconnected",
  impact: "实时更新尚未连接",
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export class ProjectionStore {
  constructor() {
    this.views = new Map();
    this.entities = new Map();
    this.pendingCommands = new Map();
    this.connection = { ...INITIAL_CONNECTION };
    this.error = null;
    this.scope = { persona_id: null, group_id: null, available_groups: [] };
    this.listeners = new Set();
  }

  merge(view) {
    if (!view || typeof view !== "object" || !view.projection) return false;
    const name = String(view.projection);
    const incomingVersion = Number(view.projection_version || 0);
    const current = this.views.get(name);
    if (current && Number(current.projection_version || 0) > incomingVersion) {
      return false;
    }
    this.views.set(name, clone(view));
    for (const item of view.items || []) {
      this.mergeEntity(item);
    }
    this.emit();
    return true;
  }

  mergeBootstrap(bootstrap) {
    this.scope = {
      persona_id: bootstrap.persona_id || null,
      group_id: bootstrap.selected_group_id || null,
      available_groups: [...(bootstrap.available_groups || [])],
    };
    for (const metadata of bootstrap.items || []) {
      this.merge({ ...metadata, items: [] });
    }
    this.emit();
  }

  mergeEntity(item) {
    if (!item || !item.entity_ref) return false;
    const key = String(item.entity_ref);
    const current = this.entities.get(key);
    const incomingVersion = Number(item.projection_version || 0);
    if (current && Number(current.projection_version || 0) >= incomingVersion) {
      return false;
    }
    this.entities.set(key, clone(item));
    return true;
  }

  applyProjectionEvent(event) {
    if (!event || !event.entity) return false;
    const applied = this.mergeEntity({
      entity_ref: event.entity,
      kind: event.kind,
      projection_version: Number(event.projection_version || 0),
      summary: clone(event.summary || {}),
      cursor: Number(event.cursor || 0),
    });
    if (applied) {
      for (const [commandId, pending] of this.pendingCommands) {
        if (Number(event.projection_version || 0) > Number(pending.expected_version || 0)) {
          this.pendingCommands.delete(commandId);
        }
      }
      this.emit();
    }
    return applied;
  }

  trackCommand(command) {
    const commandId = String(command?.command_id || "");
    if (!commandId) throw new Error("command_id is required");
    this.pendingCommands.set(commandId, clone(command));
    this.emit();
  }

  rejectCommand(commandId) {
    const removed = this.pendingCommands.delete(String(commandId));
    if (removed) this.emit();
    return removed;
  }

  setConnection(connection) {
    this.connection = clone(connection);
    this.emit();
  }

  setError(error) {
    this.error = error ? clone(error) : null;
    this.emit();
  }

  selectEntity(entityRef) {
    const item = this.entities.get(String(entityRef));
    return item ? clone(item) : null;
  }

  selectView(name) {
    const view = this.views.get(String(name));
    return view ? clone(view) : null;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit() {
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => listener(snapshot));
  }

  snapshot() {
    return {
      views: Object.fromEntries(
        [...this.views].map(([key, value]) => [key, clone(value)]),
      ),
      entities: Object.fromEntries(
        [...this.entities].map(([key, value]) => [key, clone(value)]),
      ),
      pendingCommands: [...this.pendingCommands.values()].map((item) => clone(item)),
      connection: clone(this.connection),
      error: this.error ? clone(this.error) : null,
      scope: clone(this.scope),
    };
  }
}
