# Hybrid Assistant Roadmap

## Goal

Add a grounded operations assistant to VibeView that accepts natural-language questions, uses an OpenAI-compatible provider via Xerotier.ai for intent extraction, and answers from deterministic OpenStack and Kubernetes data already available to the app.

The assistant should feel flexible in how the user asks questions, while remaining strict about:

- what data is retrieved
- how entities are resolved
- how ambiguity is handled
- which facts are allowed into the final answer

## Principles

1. Use the model for interpretation, not raw discovery.
2. Keep retrieval deterministic and scoped to the parsed intent.
3. Keep answer facts grounded in app-owned data.
4. Ask follow-up questions instead of guessing.
5. Provide actions back into the UI when possible.

## Current Foundation

The app already has:

- project inventory and quota helpers
- instance detail and project instance inventory
- networking resources including networks, routers, ports, floating IPs, load balancers, and security groups
- federated search for entity resolution
- assistant backend endpoint and minimal panel
- Xerotier/OpenAI-compatible client wiring through environment variables
- provider status and live validation

The current assistant supports a small initial set of intents:

- `quota_usage`
- `instance_status`
- `instance_location`
- `project_network_summary`

## Architecture

The assistant should continue to use a three-stage pipeline:

1. Parse
- Send the user question and small UI context to Xerotier/OpenAI-compatible parsing.
- Return a strict JSON parse:
  - `intent`
  - `scope`
  - `entities`
  - `needs_clarification`
  - `clarifying_question`

2. Resolve
- Use deterministic search and resource helpers to resolve entities.
- Require disambiguation if multiple strong matches exist.

3. Answer
- Fetch only intent-specific facts.
- Compose a grounded answer.
- Optionally use a second model call later for phrasing only, never for fact discovery.

## Context Rules

To keep prompting flexible but relevant, the assistant should never send broad cloud inventory to the model.

Instead, build narrow context by intent.

Examples:

### Instance status

Allowed facts:

- instance name
- instance ID
- project name
- status
- compute host

Not allowed:

- unrelated project inventory
- unrelated node summaries
- unrelated quota data

### Quota usage

Allowed facts:

- selected project
- quota section
- quota resource
- used
- limit

Not allowed:

- unrelated resources
- all quotas for all projects

### Security group audit

Allowed facts:

- security group identity
- attached ports and instances
- rule list
- audit summary already computed by the app

Not allowed:

- unrelated project/networking resources unless directly referenced by the security group

## Scope Defaults

Define explicit default scope rules.

1. If the user is in `Projects` and a project is selected:
- default `I` to the selected project

2. If the user names a project:
- prefer named project scope

3. If the user asks a global question outside Projects:
- either use all visible resources or ask for clarification

Recommended default:

- use selected project scope when available
- ask for clarification when no project scope is obvious

## Iteration 1: Expand Core Intents

Add these intents first:

- `quota_limit`
- `resource_count`
- `instance_networks`
- `instance_project`
- `instance_ips`
- `open_resource`
- `project_usage_summary`
- `project_storage_summary`

Example supported questions:

- "how many cores am I using?"
- "what is the limit on floating ips?"
- "which project is james-vm in?"
- "what IPs does james-vm have?"
- "open vm james-vm"
- "how many ports does this project have?"

Implementation notes:

- expand the parser schema, not just regex fallback
- keep answer handlers deterministic
- reuse federated search for resolution

## Iteration 2: Clarification and Disambiguation

Add a proper clarification flow.

### Backend

Return structured clarification when resolution is ambiguous:

```json
{
  "needs_clarification": true,
  "clarifying_question": "Which instance did you mean?",
  "choices": [
    { "kind": "instance", "id": "vm-1", "label": "james-vm", "subtext": "Project admin • cmp-a12" },
    { "kind": "instance", "id": "vm-2", "label": "james-vm-test", "subtext": "Project sandbox • cmp-a03" }
  ]
}
```

### UI

Add clickable disambiguation chips/buttons so the user does not need to retype.

### Behavior

- never answer if multiple matches are plausible
- store the selected resolution in assistant state for the next turn

## Iteration 3: Security Group Audit

Add high-value audit intents for security posture questions.

### New intents

- `security_group_audit`
- `security_group_lockdown_suggestions`
- `security_group_insecure_check`

### Example prompts

- "audit security group web-tier"
- "audit security group web-tier and suggest how to lock it down"
- "is security group default insecure?"
- "tell me if security group db-admin is risky"

### Resolution flow

1. Resolve the security group by name or ID.
2. Fetch the full security group detail and app-generated audit summary.
3. Build a narrow fact set for the answer.

### Fact set for SG audit

- security group name and ID
- project
- rule count
- ingress/egress rules
- severity from existing audit data
- open-to-world CIDRs
- admin-sensitive ports
- attachment counts
- attached instances if available

### Expected answer shape

- overall risk summary
- top 3 findings
- concrete suggestions
- actions to open the security group detail view

### Suggestion style

Suggestions should be grounded and actionable, for example:

- restrict `0.0.0.0/0` ingress on SSH to known admin ranges
- remove unused broad egress rules
- split public and internal access into separate groups
- narrow database access to application subnets only
- remove duplicate or shadowed rules

### Important rule

The model should never invent security findings.

It may phrase the answer, but the actual findings must come from deterministic audit logic already in the app or newly added deterministic checks.

## Iteration 4: Richer Prompting Profiles

Support response modes without loosening retrieval.

Suggested profiles:

- `concise`
- `explain`
- `troubleshoot`
- `recommend`

Examples:

- concise: short direct answer
- explain: include brief rationale
- troubleshoot: include likely next checks
- recommend: include prioritized actions

This keeps prompting flexible while leaving retrieval bounded.

## Iteration 5: Session and Provider UX

Improve operational trust in the assistant.

Add:

- persistent provider health/status area in the assistant panel
- last successful validation time
- parser source and fallback reasons
- clear indication when the assistant is answering from fallback parsing
- optional config UI later if env-only config is too limiting

## Suggested Parser Schema

Use a strict parse contract from the model:

```json
{
  "intent": "security_group_audit",
  "scope": "selected_project",
  "entities": [
    { "kind": "security_group", "value": "web-tier" }
  ],
  "response_mode": "recommend",
  "needs_clarification": false,
  "clarifying_question": ""
}
```

Recommended fields:

- `intent`
- `scope`
- `entities`
- `response_mode`
- `needs_clarification`
- `clarifying_question`

## Deterministic Handler Registry

The backend should route parsed intents through a registry like:

- `quota_usage`
- `quota_limit`
- `instance_status`
- `instance_location`
- `instance_networks`
- `instance_project`
- `instance_ips`
- `project_network_summary`
- `project_storage_summary`
- `security_group_audit`
- `security_group_lockdown_suggestions`
- `security_group_insecure_check`
- `open_resource`

Each handler owns:

- resolution rules
- allowed facts
- ambiguity rules
- answer/action payload

## Testing Strategy

Build a small evaluation set of real operator questions.

Categories:

- quota questions
- instance questions
- project networking summary
- security group audit
- ambiguous resource names
- missing project context
- missing provider config
- provider fallback behavior

Test both:

- parser and fallback behavior
- final deterministic answer correctness

## Immediate Next Steps

1. Expand supported intents beyond the current small set.
2. Add clarification response shapes and UI choice chips.
3. Implement security group audit intents using existing SG audit data.
4. Add answer profiles like concise vs recommend.
5. Improve provider/fallback observability in the assistant panel.

## Recommendation

Keep prompting flexible at the language layer, not the data layer.

That means:

- flexible user phrasing
- strict parse schema
- strict deterministic retrieval
- narrow fact payloads
- grounded final answers

That is the safest way to answer questions like:

- "how many floating ips am I using?"
- "what’s the status of vm james-vm?"
- "audit security group web-tier and tell me how to lock it down"
- "is security group default insecure?"
