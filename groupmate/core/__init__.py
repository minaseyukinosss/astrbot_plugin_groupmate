"""Companion Core：宿主无关的群聊伙伴运行时。"""

from .context_assembly import DYNAMIC_BLOCK_ORDER, AssembledPrompt, ContextAssembly
from .history_format import format_history_block, select_active_messages
from .mood import infer_mood
from .relationships import RelationshipEntry, parse_relationships, resolve_speaker
from .session import DialogueTurn, GroupSession, GroupSessionStore
from .speak_contract import SILENCE_MARKERS, SpeakContract, is_silence
from .voice_anchor import load_voice_anchor

__all__ = [
    "DYNAMIC_BLOCK_ORDER",
    "AssembledPrompt",
    "ContextAssembly",
    "DialogueTurn",
    "GroupSession",
    "GroupSessionStore",
    "RelationshipEntry",
    "SILENCE_MARKERS",
    "SpeakContract",
    "format_history_block",
    "infer_mood",
    "is_silence",
    "load_voice_anchor",
    "parse_relationships",
    "resolve_speaker",
    "select_active_messages",
]
