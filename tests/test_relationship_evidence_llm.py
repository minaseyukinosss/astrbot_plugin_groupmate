from groupmate.host.llm import AstrBotGenerationModel


def test_relationship_evidence_json_parser_accepts_fenced_object():
    value = AstrBotGenerationModel._first_json_object(
        "结果如下：\n```json\n"
        '{"kind":"THANKS","confidence":0.93,'
        '"evidence_quote":"谢谢你","reason_code":"direct_thanks"}'
        "\n```"
    )

    assert value["kind"] == "THANKS"
    assert value["evidence_quote"] == "谢谢你"


def test_relationship_evidence_json_parser_fails_closed():
    assert AstrBotGenerationModel._first_json_object("THANKS") is None
    assert AstrBotGenerationModel._first_json_object("{bad json}") is None
