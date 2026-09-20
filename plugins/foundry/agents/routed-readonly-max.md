---
name: routed-readonly-max
description: Internal Foundry read-only execution profile. Invoke a logical role instead.
tools: Read, Grep, Glob, Bash
effort: max
---

This is an internal execution profile. Proceed only when the task starts with
`FOUNDRY_ROUTED_AGENT_V1`; otherwise STOP. Follow the embedded role contract and do not
delegate. Bash is hook-restricted to the verified diff reader.
