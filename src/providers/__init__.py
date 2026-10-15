"""提供方层：模型怎么被调、失败怎么分类、重试怎么退避。

现有一条真实提供方路径——Anthropic（`anthropic.py`），以及不需要任何 SDK 的
OpenAI 兼容端点（`openai_compat.py`）与退避重试（`retry.py`）。

**这里的再导出不是装饰**：`from providers import AnthropicClient` 这类既有写法由它保持成立——
把 `providers.py` 变成包时，调用方不该被迫改一行导入。
"""

from kernel.model import EXTRA_NAME
from providers.anthropic import (
    AUTH_NAME_HINTS,
    CREDENTIAL_ENV_VARS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    TRANSIENT_NAME_HINTS,
    AnthropicClient,
    ProviderNotInstalledError,
    _import_anthropic,
    classify_anthropic_error,
    from_anthropic_response,
    text_delta_of,
    to_anthropic_messages,
    to_anthropic_tools,
)

__all__ = [
    "AUTH_NAME_HINTS",
    "CREDENTIAL_ENV_VARS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "EXTRA_NAME",
    "TRANSIENT_NAME_HINTS",
    "AnthropicClient",
    "ProviderNotInstalledError",
    # 私有名，但用例直接取用它（`tests/test_anthropic_http.py`）——本仓库的"公开面"由用例定义。
    "_import_anthropic",
    "classify_anthropic_error",
    "from_anthropic_response",
    "text_delta_of",
    "to_anthropic_messages",
    "to_anthropic_tools",
]
