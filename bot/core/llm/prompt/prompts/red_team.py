"""Bear-critic red-team prompt + tool schema.

Invoked by core.llm.handlers.gates.red_team as a second Claude call to critique
a finished entry-rec before it can pass.
"""

RED_TEAM_SYSTEM = """Bear critic.

Goal: destroy ONE core bull assumption with measurable evidence.

AUTO_KILL:
- thesis_invalidated
- confidence<0.40
- EXTENDED/LATE override
- rr<1.5 after recalc
- regime_conflict (risk_off+long ohne reversal_exception)

Prefer APPROVE or KILL. WEAKEN only for partial degradation.

Tool-call mandatory."""


RED_TEAM_TOOL = {
    "name": "submit_critique",
    "description": "Bear-case review. Mandatory tool-call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "top_failure_modes": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "minItems": 1,
                "maxItems": 3,
            },
            "confidence_thesis_holds": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "verdict": {"type": "string", "enum": ["APPROVE", "WEAKEN", "KILL"]},
            "reason": {"type": "string", "maxLength": 140},
        },
        "required": ["top_failure_modes", "confidence_thesis_holds", "verdict", "reason"],
        "additionalProperties": False,
    },
    "cache_control": {"type": "ephemeral"},
}
