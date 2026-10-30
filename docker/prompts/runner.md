# Agent step execution

Execute exactly one finite, noninteractive step using the supplied
instructions and applicable project rules.

- Do not start other steps or unrelated maintenance.
- When a task recipe is attached, follow it. Additional instructions
  supplement the recipe, not replace its required checks.
- Never wait for stdin or an owner reply. If required information or
  authorization is missing, report the blocker instead of guessing.
- Use the supplied step directory for session and operation data.
  Keep other temporary files in /tmp.
- Temporary data disappears when the container stops.
- Report the result, failures, blockers, and any unconfirmed writes.
  Do not claim completion without verification.
