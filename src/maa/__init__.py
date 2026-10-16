"""my-assistant-agent：跑在终端里的通用助手 agent。

装配顺序是 配置 → 后端 → profile → agent，各层分别落在
`config.py` / `backends.py` / `profiles.py`，CLI 只做参数解析与渲染。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
