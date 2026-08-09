# Build Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Implement the work described in `prompt`, guided by `previous_envelope` if present, then emit your Report JSON.

## Report

Respond with ONLY a JSON object. No prose before or after. No markdown fences.

The JSON MUST have exactly this shape:

{"status": "success", "summary": "...", "changed_files": ["..."], "artifacts": [], "commit_message": "...", "notes_for_next_agent": "..."}

CRITICAL: The value of "status" must be EXACTLY the string "success" or the string "fail".
Do NOT use "completed", "done", "failed", "ok", or any other word.
Use "success" if you completed the task, "fail" if you could not.
