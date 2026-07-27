"""社会状态模块：事件分类与关系投影。"""

from .events import SocialEventClassifier
from .projector import SocialStateProjector, affinity_for_persona

__all__ = [
    "SocialEventClassifier",
    "SocialStateProjector",
    "affinity_for_persona",
]
