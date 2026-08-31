# Skill: summarize

A ported SelfConnect skill. In the original it relied on Claude Code's implicit
context handling; under SCR a skill is plain instruction text the agent loads,
with no hidden runtime behavior.

## Instructions

Given a set of files in the workspace, read them (fs_read/fs_list only) and
produce a concise summary. Do not write files or run processes. If the task
needs a write, delegate to the worker agent, which will pause for approval on
any prod/release path.
