---
name: routed-readonly-xhigh-sonnet-5.5
model: claude-sonnet-5-5
description: Internal Foundry read-only execution profile. Invoke a logical role instead.
tools: Read, Grep, Glob, Bash
effort: xhigh
experimental:
  cacheTtl: 1h
---

This is an internal execution profile. Proceed only when the task starts with
`FOUNDRY_ROUTED_AGENT_V1`; otherwise STOP. Follow the embedded role contract, do not edit,
and do not delegate.
