from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_module(path: Path, body: str):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    script = f"const module = await import('data:text/javascript;base64,{encoded}');\n{body}"
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_dynamic_attacker_strings_are_assigned_only_as_text_content():
    result = _run_module(
        PAGE / "components" / "dom.js",
        "globalThis.document={createElement:(tag)=>({tag,children:[],dataset:{},"
        "setAttribute(){},append(value){this.children.push(value)}})};"
        "const attack='<img src=x onerror=globalThis.pwned=true><script>pwned=true</script>';"
        "const node=module.element('p',{text:attack});"
        "console.log(JSON.stringify({text:node.textContent,pwned:globalThis.pwned||false}));",
    )

    assert result == {
        "text": "<img src=x onerror=globalThis.pwned=true><script>pwned=true</script>",
        "pwned": False,
    }


def test_upload_policy_rejects_traversal_executable_mime_and_oversize_files():
    body = """
const values = [
  module.validateUpload({name:'../escape.png', type:'image/png', size:10}),
  module.validateUpload({name:'avatar.svg', type:'image/svg+xml', size:10}),
  module.validateUpload({name:'large.png', type:'image/png', size:6_000_000}),
  module.validateUpload({name:'safe.png', type:'image/png', size:1024}),
];
console.log(JSON.stringify(values));
"""
    result = _run_module(PAGE / "components" / "security.js", body)

    assert result == [
        {"accepted": False, "reason": "unsafe_filename"},
        {"accepted": False, "reason": "unsupported_mime"},
        {"accepted": False, "reason": "file_too_large"},
        {"accepted": True, "reason": None},
    ]


def test_frontend_has_no_html_execution_sink_or_protected_runtime_material():
    source = "\n".join(path.read_text(encoding="utf-8") for path in PAGE.rglob("*.js"))
    folded = source.casefold()

    for sink in ("innerhtml", "outerhtml", "insertadjacenthtml", "document.write", "eval(", "new function"):
        assert sink not in folded
    for protected in ("chain_of_thought", "system_prompt", "auth_code", "api_key"):
        assert protected not in folded


def test_operations_doc_records_failure_isolation_and_send_gate():
    document = (
        ROOT / "docs" / "operations" / "social-runtime-control-plane.md"
    ).read_text(encoding="utf-8")

    for statement in (
        "Projection/SSE 故障不阻塞 GroupSceneActor、TaskRuntime 或 Outbox",
        "真实 QQ 发送保持禁用",
        "15 秒",
        "unknown",
        "Last-Event-ID",
        "HTTP 409",
        "Gate D",
    ):
        assert statement in document
