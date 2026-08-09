# Review Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Confirm that the work reported in `previous_envelope` is what was asked for.

1. Establish the spec: read `<context_handoff_dir>/plan.md` if it exists, else use `prompt`.
2. Read the code that was actually written, starting from `previous_envelope.changed_files`.
3. Rule on every requirement in the spec — one `findings` entry each, with evidence.
4. Write the review to `<context_handoff_dir>/review.md`, then emit your Report JSON.

## Report

Respond with ONLY a JSON object. No prose before or after. No markdown fences.

The JSON MUST have exactly this shape:

{"status": "success", "approved": false, "summary": "...", "findings": [{"requirement": "...", "met": true, "evidence": "..."}], "blocking": ["..."], "artifacts": ["..."], "notes_for_next_agent": "..."}

CRITICAL: The value of "status" must be EXACTLY the string "success" or the string "fail".
Do NOT use "completed", "done", "failed", "ok", or any other word.
Use "success" if the review completed, "fail" if the review could not run.
The "approved" field is the verdict: true means approved, false means changes needed.
