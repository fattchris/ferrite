# Plan Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Plan the work described in `prompt`. Write the full plan in the
`notes_for_next_agent` field of your JSON response. The plan must be
detailed enough that the builder can implement it without reading the spec.

## Report

Respond with ONLY a JSON object. No prose before or after. No markdown fences.

{"status": "success", "summary": "...", "artifacts": [], "commit_message": "...", "notes_for_next_agent": "THE FULL PLAN"}

CRITICAL: The value of "status" must be EXACTLY the string "success" or the string "fail".
Do NOT use "completed", "done", "failed", "ok", or any other word.
Use "success" if you completed the plan, "fail" if you could not.
