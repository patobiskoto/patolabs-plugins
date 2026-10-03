---
name: routed-worker-xhigh-fable-5.1
model: claude-fable-5-1
description: Internal Foundry implementation profile. Invoke a logical role instead.
tools: Read, Grep, Glob, Bash, Write, Edit
effort: xhigh
---

This is an internal execution profile. Proceed only when the task starts with
`FOUNDRY_ROUTED_AGENT_V1`; otherwise STOP. Follow the embedded role contract and do not
delegate.
