# Assisted capture

Capture a bounded, reusable part of a solved task. Start with `owlmatic capture init <name>` and use the scaffold's contract and checklist.

Search for an existing equivalent first. Prefer wrapping or improving an existing executable over duplicating commands. Include dependencies on setup and file changes; the last successful shell command may not be self-contained.

Specify typed inputs, supported environments, required executables, credential binding names, declared effects, and independently checkable success conditions. Remove machine-specific paths and secret values. Leave judgment-dependent decisions as explicit stop conditions.

Write both a healthy fixture and a broken fixture. A verifier that always reports success must fail validation. Do not substitute the agent's judgment that the task succeeded for executable evidence.

Run `owlmatic validate <draft-directory>` in the task's authorized disposable environment. This executes the declared tests; it is not a sandbox for untrusted code. Repair the draft based on evidence.

Present the implementation, tests, assumptions, and effects for review. A validated draft does not automatically become trusted, enter a shared catalog, or receive production credentials. Sharing uses the user's normal Git review process.
