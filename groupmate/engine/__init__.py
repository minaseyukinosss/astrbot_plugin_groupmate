"""调度引擎：触发、话题窗、认知工作流、投递。"""

from importlib import import_module


_EXPORTS = {
    "CognitiveWorkflow": (".workflow", "CognitiveWorkflow"),
    "GroupActor": (".runtime", "GroupActor"),
    "GroupRuntimeManager": (".runtime", "GroupRuntimeManager"),
    "SlidingWindowRateLimiter": (".rate_limit", "SlidingWindowRateLimiter"),
    "TopicWindow": (".topics", "TopicWindow"),
    "TriggerRouter": (".triggers", "TriggerRouter"),
    "build_delivery_plan": (".delivery", "build_delivery_plan"),
    "delivery_still_valid": (".delivery", "delivery_still_valid"),
    "needs_external_knowledge": (
        ".external_knowledge",
        "needs_external_knowledge",
    ),
    "select_active_messages": (".topics", "select_active_messages"),
}

__all__ = [
    "CognitiveWorkflow",
    "GroupActor",
    "GroupRuntimeManager",
    "SlidingWindowRateLimiter",
    "TopicWindow",
    "TriggerRouter",
    "build_delivery_plan",
    "delivery_still_valid",
    "needs_external_knowledge",
    "select_active_messages",
]


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError("module {!r} has no attribute {!r}".format(
            __name__, name
        ))
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
