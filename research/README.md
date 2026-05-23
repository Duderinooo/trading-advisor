# Research Log

Long-form incident postmortems + tuning rationale. One file per incident /
behavior change worth preserving the "why" for.

**Format:**
- Date (ISO)
- Symptom
- Root cause
- Fix (link to commit if possible)
- Constants changed
- Lessons

**Why this folder exists:**
Code-comments rot; long inline `# Bug 2026-MM-DD …` blocks become noise that
nobody re-reads. By extracting context here, the code stays scannable, and the
"why" remains discoverable via grep + git log.

**Rule:** when tuning a config constant or changing a gate threshold, add an
entry here. Reference the doc from a one-line code comment at the constant.

Pointer-format in code (mandatory next to the changed constant):
```python
# 2026-05-12: see research/edge-floor-006.md
MIN_EXPECTED_EDGE = 0.06
```
