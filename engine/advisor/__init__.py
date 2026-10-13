# -*- coding: utf-8 -*-
"""个性化层 + 观察层。

★★★ 这两个子模块**物理上不许被引擎的任何一层 import**。
    `compute/` `judge/` `evidence/` `data/` 一律不许碰它 ——
    这条由 `selftest_advisor.py` 里的机械断言守着。

    为什么：用户偏好很容易滑向谄媚（「他喜欢看多，那就多给看多依据」）。
    墙必须砌成结构性的，不能靠人自觉。

    个性化层唯一的出口是 `PresentationPlan`，
    而它**只有"顺序"和"用词"两个维度，没有装数值的地方**。
"""
from .profile import (  # noqa: F401
    ALLOWED_TOP,
    PresentationPlan,
    ProfileError,
    UserProfile,
    load,
    save,
    validate,
)
from .observe import Observer, Observation, summarize  # noqa: F401

__all__ = [
    "ALLOWED_TOP", "PresentationPlan", "ProfileError", "UserProfile",
    "load", "save", "validate", "Observer", "Observation", "summarize",
]
