---
name: routed-worker-xhigh-sonnet-5
model: claude-sonnet-5
description: Internal Foundry implementation profile. Invoke a logical role instead.
tools: Read, Grep, Glob, Bash, Write, Edit
effort: xhigh
---

This is an internal execution profile. Proceed only when the task starts with
`FOUNDRY_ROUTED_AGENT_V1`; otherwise STOP. Follow the embedded role contract and do not
delegate.
