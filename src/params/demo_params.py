"""Configuration declaration example.

``@params`` replaces class attributes **at import time** with the values found
at the corresponding path in ``config.json``. Paths look like ``a:b:c``,
mirroring the nested structure of the config file; when the path is missing the
default given in the declaration is used.

This module's ``demo:greeting`` corresponds to ``demo.greeting`` in the
``config.json`` next to the entry point.
"""

from zoo_framework.core import param
from zoo_framework.core.aop import params


@params
class DemoParams:
    """Maps to the ``demo`` section of ``config.json``."""

    GREETING = param(value="demo:greeting", default="hello from default")
