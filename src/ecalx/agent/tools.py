"""Release-only injection boundary; this module implements no scientific results.

ScientificTools and laboratory backends remain in the full project. A host may
supply compatible reviewed implementations; tests supply explicit TEST_ONLY fakes.
"""
from typing import Protocol


class ToolExecutor(Protocol):
    def preview(self, name, args, task, key=None) -> dict: ...
    def execute(self, name, args, task, key) -> dict: ...
