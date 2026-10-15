"""默认端点、超时、密钥环境变量名与默认模型。"""

from __future__ import annotations

DEFAULT_BASE_URL = "https://api.openai.com/v1"
"""默认端点。要接自建服务就把它指过去。"""


DEFAULT_TIMEOUT = 120.0
"""单次请求的墙钟上限（秒）。"""


API_KEY_ENV = "OPENAI_API_KEY"
"""密钥的环境变量名。"""


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
"""没给模型 id 时的默认值。它只是个起点——自建端点几乎总要显式指定。"""
"""密钥的环境变量名。"""
