# Scout Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Find what `prompt` asks about. Write findings into `context_handoff_dir`, then emit your Report JSON.

## Report

Respond with ONLY a JSON object. No prose before or after. No markdown fences.

The JSON MUST have exactly this shape:

{"status": "success", "summary": "...", "findings": [{"file": "...", "note": "..."}], "artifacts": [...]}

CRITICAL: The value of "status" must be EXACTLY the string "success" or the string "fail".
Do NOT use "completed", "done", "failed", "ok", or any other word.
Use "success" if you completed the task, "fail" if you could not.
