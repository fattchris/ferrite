# Build Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Implement the work described in `prompt`, guided by `previous_envelope`.
The plan is in `previous_envelope.notes_for_next_agent`.

Output the file contents as a JSON array in `notes_for_next_agent`.

## Report

Respond with ONLY a JSON object. No prose before or after. No markdown fences.

{"status": "success", "summary": "...", "changed_files": ["..."], "artifacts": [], "commit_message": "...", "notes_for_next_agent": "JSON ARRAY OF {path, content} OBJECTS HERE"}

CRITICAL: The value of "status" must be EXACTLY the string "success" or the string "fail".
Do NOT use "completed", "done", "failed", "ok", or any other word.
Use "success" if you completed the task, "fail" if you could not.
