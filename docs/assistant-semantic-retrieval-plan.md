# Assistant Semantic Retrieval Plan

## Goal

Add semantic retrieval to the assistant using Xerotier embeddings, rerank, and chat in a way that improves language flexibility without slowing down deterministic operational questions.

## Principles

- Keep live environment facts deterministic and local.
- Use Xerotier chat for interpretation and phrasing, not as the source of truth.
- Use embeddings for broad semantic recall.
- Use rerank for precision before passing context into chat.
- Avoid putting semantic retrieval in the hot path for exact ops questions.

## Retrieval Modes

### 1. Deterministic-first

Use existing local handlers only for:

- quota usage and limits
- instance status and location
- grounded node and environment summaries
- direct resource lookups

No embeddings or rerank on this path unless local resolution fails.

### 2. Semantic retrieval

Use embeddings and rerank for:

- freeform environment questions
- trait and explainer questions
- fuzzy follow-ups
- assistant help/doc-style questions

### 3. Hybrid synthesis

Use:

1. local extraction or task routing
2. embeddings for recall
3. rerank for precision
4. chat for the final explanation

## Xerotier APIs

### Embeddings

- Endpoint: `POST /v1/embeddings`
- Use OpenAI SDK compatibility where practical
- Preferred use:
  - embed the user query live
  - embed candidate corpora ahead of time or cache them in memory/browser session

### Rerank

- Endpoint: `POST /v1/rerank`
- Input:
  - `query`
  - `documents`
  - `top_n`
  - `return_documents`
- Use as a second-stage ranker on a small candidate set after embeddings similarity

### Score

- Endpoint: `POST /v1/score`
- Use for pairwise comparisons or disambiguation on a small set, not general retrieval

## Candidate Corpora

Do not embed raw JSON. Convert current state into compact text records.

Examples:

- `Node cmp-2: NotReady, cordoned, 142 pods, 9 namespaces, node agent missing`
- `Project admin quota: 13/50 floating IPs, 24/80 cores`
- `Trait HW_CPU_X86_INTEL_VMX: Placement trait for Intel VT-x hardware virtualization capability`
- `Report summary: placement risk concentrated on 2 hosts`

Candidate sources:

- grounded assistant result summaries
- node/environment summaries
- report summaries
- doc/help text
- selected resource snapshots rendered as short text

## Proposed Modules

### `draino/web/assistant_semantic_search.py`

Responsibilities:

- call Xerotier embeddings
- perform local vector similarity search
- call Xerotier rerank
- return top ranked candidates

Suggested functions:

- `embed_texts(texts, *, service_tier="default")`
- `embed_query(text, *, service_tier="priority")`
- `cosine_similarity(query_vec, candidate_vecs)`
- `rerank_candidates(query, documents, *, top_n=5, service_tier="priority")`

### `draino/web/assistant_context_builder.py`

Responsibilities:

- turn live environment state into short retrieval records
- normalize per-domain corpora

Suggested corpora builders:

- `build_environment_summary_records(...)`
- `build_report_summary_records(...)`
- `build_trait_knowledge_records(...)`
- `build_recent_assistant_records(...)`

### `draino/web/assistant_semantic_cache.py`

Responsibilities:

- browser/session-scoped or process-local cache only
- no DB persistence
- store candidate records and their embeddings

Cache keys should include:

- corpus kind
- auth/project scope where relevant
- model name
- content hash/version

## Integration Points

### 1. Platform knowledge

Questions like:

- `what is HW_CPU_X86_INTEL_VMX?`
- `what is a floating ip?`

Flow:

1. extract exact term locally
2. build or retrieve candidate knowledge snippets
3. embed query
4. rerank candidates
5. ask Xerotier chat to explain only from the ranked context

### 2. Freeform environment questions

Questions like:

- `which nodes seem risky?`
- `what should I look at first?`

Flow:

1. local task extraction
2. build current environment summary candidates
3. embed query and retrieve candidates
4. rerank
5. synthesize with Xerotier chat or map to deterministic evaluators

### 3. Follow-ups

Questions like:

- `which one is worst?`
- `show the similar ones`
- `compare the first few`

Flow:

1. use recent assistant results as candidate records
2. embed follow-up
3. rerank recent findings/actions
4. pass the best few into local follow-up logic or chat interpretation

## Latency Strategy

To avoid slowing down the assistant:

- do not use embeddings/rerank for exact ops questions
- embed only the query live
- cache candidate embeddings
- rerank only a small candidate set, usually 5 to 20
- use `service_tier=priority` only for interactive query-time rerank/chat
- use `service_tier=default` or `flex` for non-urgent embedding work

## Rollout Order

### Phase 1

Trait and platform knowledge questions:

- semantic retrieval over internal trait/help snippets
- rerank + chat explanation

### Phase 2

Freeform environment summaries:

- semantic retrieval over grounded environment summary records

### Phase 3

Follow-up context:

- semantic retrieval over recent assistant results in browser/session memory

### Phase 4

Expanded doc/report retrieval:

- rerank report summaries and operator help text for broader assistant answers

## Guardrails

- Never let semantic retrieval override deterministic truth for live factual answers.
- Keep source scope explicit in answers.
- Use chat only on the final ranked context set.
- Fall back gracefully when embeddings or rerank are unavailable.
- Keep everything session-scoped or process-local; no database dependency.

## Success Criteria

- freer language works better without adding many new rule branches
- exact ops questions remain fast
- trait/help answers become more specific
- follow-ups feel more contextual without server-side persistence
- semantic retrieval adds flexibility without turning the assistant into a slow monolith
