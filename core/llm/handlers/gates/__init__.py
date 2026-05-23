"""Engine gate pipeline for recommend_entry recs.

Each gate function returns bool (True=pass, False=block) and may mutate entry
in-place. run_entry_gates chains them in fixed order; full pass returns the
mutated entry, first block returns None.
"""

from core.llm.handlers.gates.context import (
    GateContext, build_gate_context, run_entry_gates,
)

__all__ = ["GateContext", "build_gate_context", "run_entry_gates"]
