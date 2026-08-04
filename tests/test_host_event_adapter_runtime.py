from dataclasses import FrozenInstanceError, replace

import pytest

import groupmate.host as public_host
from groupmate.host.event_adapters import (
    HostEventAdapter,
    HostEventAdapterManifest,
    HostEventAdapterResult,
    HostEventAdapterRuntime,
    HostEventAdapterStatus,
)
from groupmate.models import ChatMessage, MessageOrigin


def synthetic_message(**metadata):
    return ChatMessage(
        message_id="synthetic-1",
        group_id="group-1",
        sender_id="user-1",
        sender_name="User",
        text="synthetic interaction",
        timestamp=123,
        metadata=metadata,
        origin=MessageOrigin.SYSTEM_SYNTHETIC,
    )


def adapter_type(name, event_kinds, adapt):
    return type(
        "TestAdapter_{}".format(name),
        (HostEventAdapter,),
        {
            "manifest": HostEventAdapterManifest(name, event_kinds),
            "adapt": adapt,
        },
    )


def test_manifest_is_frozen_and_normalizes_name_and_event_kinds():
    manifest = HostEventAdapterManifest(
        "  OneBot-Poke  ",
        (" NOTICE.POKE ", "notice.poke", " META.EVENT "),
    )

    assert manifest.name == "onebot-poke"
    assert manifest.event_kinds == (
        "notice.poke",
        "meta.event",
    )
    with pytest.raises(FrozenInstanceError):
        manifest.name = "other"


@pytest.mark.parametrize(
    "name,event_kinds",
    [
        ("", ("notice.poke",)),
        ("   ", ("notice.poke",)),
        ("onebot-poke", ()),
        ("onebot-poke", ("  ",)),
        ("onebot-poke", ("notice.poke", "  ")),
        ("onebot-poke", "notice.poke"),
    ],
)
def test_manifest_rejects_empty_name_or_event_kinds(name, event_kinds):
    with pytest.raises(ValueError):
        HostEventAdapterManifest(name, event_kinds)


def test_result_is_frozen_and_normalizes_status_and_reason():
    result = HostEventAdapterResult(" admitted ", " ADMITTED ", synthetic_message())

    assert result.status is HostEventAdapterStatus.ADMITTED
    assert result.reason_code == "admitted"
    with pytest.raises(FrozenInstanceError):
        result.reason_code = "other"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_result_rejects_empty_reason(reason):
    with pytest.raises(ValueError):
        HostEventAdapterResult(HostEventAdapterStatus.BYPASSED, reason)


def test_admitted_result_requires_system_synthetic_chat_message():
    realtime = replace(
        synthetic_message(),
        origin=MessageOrigin.PLATFORM_REALTIME,
    )

    with pytest.raises(ValueError):
        HostEventAdapterResult.admitted(realtime)
    with pytest.raises(ValueError):
        HostEventAdapterResult.admitted(object())


@pytest.mark.parametrize(
    "status",
    [HostEventAdapterStatus.NOT_MATCHED, HostEventAdapterStatus.BYPASSED],
)
def test_non_admitted_result_cannot_carry_message(status):
    with pytest.raises(ValueError):
        HostEventAdapterResult(status, status.value, synthetic_message())


def test_admitted_result_allows_only_string_contract_metadata():
    message = synthetic_message(
        interaction_kind="poke",
        target_id="bot-1",
        source_adapter="onebot-poke",
    )

    assert HostEventAdapterResult.admitted(message).message is message

    with pytest.raises(ValueError, match="metadata"):
        HostEventAdapterResult.admitted(
            replace(
                message,
                metadata={"interaction_kind": "poke", "raw": object()},
            )
        )
    with pytest.raises(ValueError, match="metadata"):
        HostEventAdapterResult.admitted(
            replace(
                message,
                metadata={
                    "interaction_kind": "poke",
                    "correlation_id": "request-1",
                },
            )
        )
    with pytest.raises(ValueError, match="metadata"):
        HostEventAdapterResult.admitted(
            replace(message, metadata={"interaction_kind": object()})
        )
    with pytest.raises(ValueError, match="metadata"):
        HostEventAdapterResult.admitted(
            replace(message, metadata={1: "poke"})
        )


def test_result_factories_supply_contract_reason_codes():
    assert HostEventAdapterResult.not_matched() == HostEventAdapterResult(
        HostEventAdapterStatus.NOT_MATCHED,
        "not_matched",
    )
    assert HostEventAdapterResult.bypassed(" disabled ") == HostEventAdapterResult(
        HostEventAdapterStatus.BYPASSED,
        "disabled",
    )
    assert HostEventAdapterResult.admitted(synthetic_message()).reason_code == "admitted"


def test_base_adapter_requires_valid_manifest():
    class MissingManifestAdapter(HostEventAdapter):
        def adapt(self, event):
            return HostEventAdapterResult.not_matched()

    class InvalidManifestAdapter(HostEventAdapter):
        manifest = "invalid"

        def adapt(self, event):
            return HostEventAdapterResult.not_matched()

    with pytest.raises((TypeError, ValueError)):
        MissingManifestAdapter()
    with pytest.raises((TypeError, ValueError)):
        InvalidManifestAdapter()


def test_base_adapter_accepts_instance_manifest_before_validation():
    class InstanceManifestAdapter(HostEventAdapter):
        def __init__(self):
            self.manifest = HostEventAdapterManifest(
                "instance",
                ("instance.event",),
            )
            super().__init__()

        def adapt(self, event):
            return HostEventAdapterResult.not_matched()

    adapter = InstanceManifestAdapter()

    assert adapter.manifest.name == "instance"


def test_base_adapter_manifest_validation_cannot_be_bypassed_by_init():
    class InvalidManifestAdapter(HostEventAdapter):
        manifest = "invalid"

        def __init__(self):
            pass

        def adapt(self, event):
            return HostEventAdapterResult.not_matched()

    with pytest.raises(TypeError, match="valid manifest"):
        InvalidManifestAdapter()


def test_base_adapter_remains_abstract_without_adapt():
    class IncompleteAdapter(HostEventAdapter):
        manifest = HostEventAdapterManifest("incomplete", ("incomplete",))

    with pytest.raises(TypeError):
        IncompleteAdapter()


def test_runtime_stores_immutable_adapters_and_rejects_duplicates():
    first = adapter_type(
        "first",
        ("kind.first",),
        lambda self, event: HostEventAdapterResult.not_matched(),
    )()
    second = adapter_type(
        "second",
        ("kind.second",),
        lambda self, event: HostEventAdapterResult.not_matched(),
    )()

    runtime = HostEventAdapterRuntime((first, second))

    assert runtime.adapters == (first, second)
    assert isinstance(runtime.adapters, tuple)

    duplicate_name = adapter_type(
        "first",
        ("kind.third",),
        lambda self, event: HostEventAdapterResult.not_matched(),
    )()
    with pytest.raises(ValueError, match="duplicate adapter name"):
        HostEventAdapterRuntime((first, duplicate_name))

    duplicate_kind = adapter_type(
        "third",
        ("kind.first",),
        lambda self, event: HostEventAdapterResult.not_matched(),
    )()
    with pytest.raises(ValueError, match="duplicate event kind"):
        HostEventAdapterRuntime((first, duplicate_kind))


def test_runtime_rejects_non_adapter_values():
    with pytest.raises((TypeError, ValueError)):
        HostEventAdapterRuntime((object(),))


def test_runtime_dispatches_in_order_until_adapter_claims_event():
    calls = []

    def not_matched(self, event):
        calls.append(("first", event))
        return HostEventAdapterResult.not_matched()

    def admitted(self, event):
        calls.append(("second", event))
        return HostEventAdapterResult.admitted(synthetic_message())

    def must_not_run(self, event):
        calls.append(("third", event))
        return HostEventAdapterResult.bypassed("too_late")

    runtime = HostEventAdapterRuntime(
        (
            adapter_type("first", ("kind.first",), not_matched)(),
            adapter_type("second", ("kind.second",), admitted)(),
            adapter_type("third", ("kind.third",), must_not_run)(),
        )
    )
    event = object()

    result = runtime.adapt(event)

    assert result.status is HostEventAdapterStatus.ADMITTED
    assert calls == [("first", event), ("second", event)]


def test_runtime_stops_on_bypassed_result():
    calls = []

    def bypassed(self, event):
        calls.append("first")
        return HostEventAdapterResult.bypassed("unsupported")

    def must_not_run(self, event):
        calls.append("second")
        return HostEventAdapterResult.not_matched()

    runtime = HostEventAdapterRuntime(
        (
            adapter_type("first", ("kind.first",), bypassed)(),
            adapter_type("second", ("kind.second",), must_not_run)(),
        )
    )

    assert runtime.adapt(object()).reason_code == "unsupported"
    assert calls == ["first"]


@pytest.mark.parametrize("failure", [RuntimeError("boom"), object()])
def test_runtime_fails_closed_on_exception_or_invalid_result(failure):
    def fail(self, event):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    runtime = HostEventAdapterRuntime(
        (adapter_type("failing", ("kind.failure",), fail)(),)
    )

    assert runtime.adapt(object()) == HostEventAdapterResult.bypassed(
        "adapter_error"
    )


def test_runtime_revalidates_forged_result_instances_at_boundary():
    uninitialized = object.__new__(HostEventAdapterResult)
    forged_admitted = object.__new__(HostEventAdapterResult)
    object.__setattr__(forged_admitted, "status", HostEventAdapterStatus.ADMITTED)
    object.__setattr__(forged_admitted, "reason_code", "admitted")
    object.__setattr__(forged_admitted, "message", None)

    for result in (uninitialized, forged_admitted):
        def return_result(self, event):
            return result

        runtime = HostEventAdapterRuntime(
            (adapter_type("forged", ("kind.forged",), return_result)(),)
        )

        assert runtime.adapt(object()) == HostEventAdapterResult.bypassed(
            "adapter_error"
        )


def test_empty_runtime_returns_not_matched():
    result = HostEventAdapterRuntime().adapt(object())

    assert result.status is HostEventAdapterStatus.NOT_MATCHED
    assert result.reason_code == "not_matched"


def test_contract_and_runtime_are_available_from_public_host_surface():
    assert public_host.HostEventAdapter is HostEventAdapter
    assert public_host.HostEventAdapterManifest is HostEventAdapterManifest
    assert public_host.HostEventAdapterResult is HostEventAdapterResult
    assert public_host.HostEventAdapterRuntime is HostEventAdapterRuntime
    assert public_host.HostEventAdapterStatus is HostEventAdapterStatus
