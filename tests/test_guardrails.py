import pytest

from groupmate.capabilities.contracts import CapabilityStatus
from groupmate.core.response_act import ResponseAct
from groupmate.persona.aemeath import AemeathOutputFirewall


@pytest.mark.parametrize(
    "text,code",
    [
        ("(没人叫我，不回复)", "decision_narration"),
        ("(擦了擦汗，顺手拿起水瓶喝了一大口)", "decision_narration"),
        ("刚跑完训练场 累死我了（擦了擦汗）", "decision_narration"),
        ("*歪了歪头*", "decision_narration"),
        ("有什么可以帮你的吗？", "customer_service_template"),
        ("prompt 调好了就行", "system_vocabulary"),
        ("我是一个 AI 语言模型", "technical_identity_disclosure"),
        ("其实是机器人啦", "technical_identity_disclosure"),
        ("我是爱弥斯", "canned_identity_reply"),
        ("就是一直在群里的爱弥斯呀", "canned_identity_reply"),
        ("你呢？", "forced_followup"),
        ("这周末你呢？", "forced_followup"),
        ("工作上你呢？", "forced_followup"),
        ("怎么啦？", "forced_followup"),
        ("怎么啦 ？", "forced_followup"),
        ("怎么啦？ ！", "forced_followup"),
        ("怎么了？", "forced_followup"),
        ("怎么啦...", "forced_followup"),
        ("怎么啦……", "forced_followup"),
        ("然后呢。", "forced_followup"),
        ("那然后呢？", "forced_followup"),
        ("我在呢，怎么啦？", "forced_followup"),
        ("我在呢.怎么啦？", "forced_followup"),
        ("我在呢...怎么了？", "forced_followup"),
        ("我在呢……怎么啦？", "forced_followup"),
        ("我在呢, 怎么啦?", "forced_followup"),
        ("我在呢， 怎么啦？", "forced_followup"),
        ("上一句\n  怎么了？", "forced_followup"),
        ("嗯，然后呢。", "forced_followup"),
        ("有什么想聊的吗？", "forced_followup"),
    ],
)
def test_aemeath_guard_rejects_known_failures(text, code):
    result = AemeathOutputFirewall().validate(text, recent_outputs=[])

    assert result.accepted is False
    assert code in result.codes


def test_guard_rejects_screenshot_style_rp_blob():
    text = (
        "刚跑完训练场 累死我了\n"
        "(擦了擦汗，顺手拿起水瓶喝了一大口)\n"
        "体能课真是要命 我现在只想躺平"
    )
    result = AemeathOutputFirewall().validate(text, [])
    assert result.accepted is False
    assert "decision_narration" in result.codes or "too_many_sentences" in result.codes


def test_guard_accepts_required_clarifying_question():
    result = AemeathOutputFirewall().validate("你用的是哪个版本？", [])

    assert result.accepted is True


def test_guard_accepts_firm_response_without_hostility_escalation():
    result = AemeathOutputFirewall().validate(
        "必要的事我会答，别的先免了。", []
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    "text,code",
    [
        ("嘿，戳人的家伙被反戳咯～", "leading_mono_interjection"),
        ("噗，你俩这是戳上瘾了嘛～", "leading_mono_interjection"),
        ("哦，那我知道了", "leading_mono_interjection"),
        ("哦对了", "leading_mono_interjection"),
        ("啊这样啊", "leading_mono_interjection"),
        ("别戳啦——再戳我可要让你看看后果哦！", "decorative_punctuation"),
        ("你戳戳我戳戳～", "decorative_punctuation"),
    ],
)
def test_guard_rejects_chatty_poke_style(text, code):
    result = AemeathOutputFirewall().validate(
        text,
        [],
        response_act=ResponseAct.PLAYFUL_REPLY,
    )

    assert result.accepted is False
    assert code in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "嗯",
        "在",
        "行",
        "好，那我去看看",
        "眼光不错哦。",
        "别戳啦有事快说",
    ],
)
def test_guard_allows_short_ack_and_normal_openers(text):
    result = AemeathOutputFirewall().validate(
        text,
        [],
        response_act=ResponseAct.PLAYFUL_REPLY,
    )

    assert result.accepted is True


def test_guard_allows_group_address_on_playful_reply():
    result = AemeathOutputFirewall().validate(
        "你们复读也太整齐了吧",
        [],
        response_act=ResponseAct.PLAYFUL_REPLY,
    )

    assert result.accepted is True


def test_guard_accepts_plain_poke_pushback():
    result = AemeathOutputFirewall().validate(
        "别戳啦有事快说",
        [],
        response_act=ResponseAct.PLAYFUL_REPLY,
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    "text",
    [
        "这个配置怎么了？",
        "你用的这个版本怎么了？",
        "Python 怎么了？",
        "v2 怎么了？",
    ],
)
def test_guard_accepts_specific_clarifying_questions(text):
    result = AemeathOutputFirewall().validate(text, recent_outputs=[])

    assert result.accepted is True


@pytest.mark.parametrize(
    "text",
    [
        "眼光不错哦。",
        "今天早点休息，别硬撑啦。",
        "才不是你老婆呢，少乱叫呀。",
    ],
)
def test_guard_accepts_natural_persona_replies(text):
    result = AemeathOutputFirewall().validate(text, recent_outputs=[])

    assert result.accepted is True


def test_guard_accepts_short_natural_reply():
    result = AemeathOutputFirewall().validate(
        "这也太离谱了呀。", recent_outputs=[]
    )

    assert result.accepted is True
    assert result.text == "这也太离谱了呀。"


def test_guard_rejects_recent_duplicate():
    result = AemeathOutputFirewall().validate(
        "这也太离谱了呀。", recent_outputs=["这也太离谱了呀。"]
    )

    assert "duplicate_output" in result.codes
    assert result.repairable is False


@pytest.mark.parametrize(
    ("act", "status"),
    (
        (ResponseAct.TASK_UNSUPPORTED, CapabilityStatus.UNSUPPORTED),
        (ResponseAct.TASK_HANDOFF, CapabilityStatus.HANDOFF),
        (ResponseAct.TASK_HANDOFF, CapabilityStatus.FAILED),
        (ResponseAct.TASK_HANDOFF, CapabilityStatus.TIMEOUT),
        (ResponseAct.TASK_HANDOFF, None),
    ),
)
def test_guard_rejects_false_task_completion_before_success(act, status):
    result = AemeathOutputFirewall().validate(
        "已经帮你查好了。",
        (),
        response_act=act,
        capability_status=status,
    )

    assert result.accepted is False
    assert "false_task_completion" in result.codes
    assert result.repairable is True


def test_guard_allows_completion_fact_after_capability_success():
    result = AemeathOutputFirewall().validate(
        "已经识别好了，图里是一盆花。",
        (),
        response_act=ResponseAct.TASK_HANDOFF,
        capability_status=CapabilityStatus.SUCCESS,
    )

    assert "false_task_completion" not in result.codes


def test_guard_rejects_future_capability_promise_without_verified_execution():
    result = AemeathOutputFirewall().validate(
        "我会帮你查好再发给你。",
        (),
        response_act=ResponseAct.ANSWER,
        capability_status=None,
    )

    assert result.accepted is False
    assert "unsupported_future_promise" in result.codes


def test_guard_allows_future_capability_wording_after_success():
    result = AemeathOutputFirewall().validate(
        "我来帮你查，结果已经出来了。",
        (),
        response_act=ResponseAct.TASK_HANDOFF,
        capability_status=CapabilityStatus.SUCCESS,
    )

    assert "unsupported_future_promise" not in result.codes


def test_guard_allows_a_scheduled_reminder_promise():
    result = AemeathOutputFirewall().validate(
        "好，周五下午我提醒你交材料。",
        (),
        response_act=ResponseAct.ANSWER,
        capability_status=None,
    )

    assert "unsupported_future_promise" not in result.codes


def test_guard_rejects_premature_timed_reminder_delivery():
    rejected = AemeathOutputFirewall().validate(
        "交材料了",
        [],
        source_text="小爱，1分钟后提醒我交材料",
    )
    assert rejected.accepted is False
    assert "premature_reminder_delivery" in rejected.codes
    assert rejected.repairable is True

    accepted = AemeathOutputFirewall().validate(
        "好嘞，1分钟倒计时开始哦",
        [],
        source_text="小爱，1分钟后提醒我交材料",
    )
    assert accepted.accepted is True
    assert "premature_reminder_delivery" not in accepted.codes


def test_guard_premature_stays_repairable_even_if_duplicate():
    result = AemeathOutputFirewall().validate(
        "交材料了",
        ["交材料了"],
        source_text="小爱，1 分钟后提醒我交材料",
    )
    assert result.accepted is False
    assert "premature_reminder_delivery" in result.codes
    assert "duplicate_output" in result.codes
    assert result.repairable is True


def test_guard_does_not_treat_unrelated_due_phrase_as_premature():
    result = AemeathOutputFirewall().validate(
        "该吃饭了",
        [],
        source_text="小爱，1分钟后提醒我交材料",
    )
    assert "premature_reminder_delivery" not in result.codes
