"""AgentCore Platform v1.0"""

# FIN-C2-007 — Financial Report Generation Agent (flat Category 2 pipeline).
#
# Backbone (fixed by the framework base class):
#
#   START -> initialize -> pre_process -> main -> {route} -> post_process
#                                          |                      |
#                                          +-- RETRY -> pre_process
#                                                              -> finalize -> END
#
# Slot mapping:
#   pre_process   PreProcessNode        external input boundary
#   main          ReportGenerationNode  validate -> select template -> render
#   post_process  PostProcessNode       output boundary; writes formatted_output
#
# The class name must match in three places: this file, the `class` entry in
# config/agent.yaml, and the import in src/api/server.py.

import math
from pathlib import Path
from typing import Any, Dict, Optional

from framework.graph.agent_base_graph import AgentBaseGraph
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.report_generation_node import ReportGenerationNode
from src.schemas.state import State

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Accepted bounds for the declared runtime parameters.
_MAX_RETRY_BOUNDS = (0, 10)
_TIMEOUT_BOUNDS = (1, 3600)


def runtime_config() -> Dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    This is the same file the platform registry loads and passes as
    Graph(config=...); the standalone server reads it here so a deployed agent
    and a registry-loaded agent see identical configuration.

    Never raises: an absent, unreadable, malformed or non-mapping file yields
    an empty dict and the graph runs on its built-in defaults.  Declared values
    are validated before they are handed on, so a malformed configuration file
    cannot make a retry bound non-comparable — a non-finite max_retry would
    compare False against every retry count and silently disable retries.
    """
    try:
        import yaml

        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}

    config: Dict[str, Any] = {}
    max_retry = _bounded_int(loaded.get("max_retry"), *_MAX_RETRY_BOUNDS)
    if max_retry is not None:
        config["max_retry"] = max_retry
    timeout_s = _bounded_int(loaded.get("timeout_s"), *_TIMEOUT_BOUNDS)
    if timeout_s is not None:
        config["timeout_s"] = timeout_s
    for key in ("memory_enabled", "hitl"):
        if key in loaded:
            config[key] = loaded[key]
    return config


def _bounded_int(value: Any, low: int, high: int) -> Optional[int]:
    """Return *value* as an int when it is a real, finite number in [low, high].

    Booleans, strings, non-numerics, NaN/Infinity and out-of-range values all
    return None, and the caller then keeps the framework default.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    if not low <= value <= high:
        return None
    return int(value)


class FinC2007Agent(AgentBaseGraph):
    """Financial Report Generation Agent.

    Generates structured financial reports — risk assessment, credit summary,
    portfolio performance, compliance status — from caller-supplied financial
    data using configurable Markdown document templates.

    Backbone: initialize -> pre_process -> main -> post_process -> finalize
    """

    @property
    def name(self) -> str:
        return "FinC2007Agent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # fills the initialize and finalize slots

        # External input boundary — verified external callers only.
        self._nodes["pre_process"] = PreProcessNode()

        # Domain pipeline: validate the caller's data, select the document
        # template, render the report.
        self._nodes["main"] = ReportGenerationNode()

        # Output boundary — writes formatted_output.
        self._nodes["post_process"] = PostProcessNode()
