# Multi-User Scaling Design Without a Database

## Implementation Status (2026-10)

**Implemented (Lease coordination, this repo):**

- `draino/operations/k8s_ops.py`: Lease acquire (atomic 409-create), expired-lease
  takeover with re-fetch race guard, owner-guarded renew/release, `_lock_namespace()`
  from serviceaccount namespace / `DRAINO_POD_NAMESPACE`.
- `draino/web/op_locks.py`: `OperationLockManager` with holder identity
  (pod.user.session), background renewal (`DRAINO_LEASE_SECONDS=60`, renew at 1/3 TTL),
  lock-loss tracking, and `guard()` context manager. `DRAINO_DISABLE_OP_LOCKS=1`
  disables real Lease calls (tests only).
- Maintenance entrypoints acquire per-node Leases before starting, with an
  operator-visible "already running elsewhere" log + audit entry on contention:
  `drain`, `undrain`, `evacuate`, `enable_nova`, `reboot` (naming
  `vibeview-op-<action>-<node>`). Lock loss mid-workflow aborts the workflow
  honestly (`lock_lost_fn` checked in reboot/evacuate poll loops; drain/undrain
  are short enough that guard-release + abort-on-next-step suffices) — a
  partitioned pod never keeps mutating infrastructure another replica owns.
- Clone endpoint acquires a per-instance Lease (`vibeview-op-clone-<sha256-16>`)
  with renewal during the workflow and release in `finally`; the in-process
  clone-status map remains only as a convenience layer.
- RBAC: namespaced Role grants `coordination.k8s.io/leases`
  create/get/list/update/patch/delete.
- Session close releases all Leases held by that session; pod death falls back
  to lease expiry (≤60s), matching the failure model.

**Not implemented (open decisions):**

- ~~Session strategy~~ **Decided: sticky sessions.** The web Service ships
  `sessionAffinity: ClientIP` (3600s timeout) in the chart, so `replicaCount`
  can be raised above 1 with browser sessions pinned per client IP. Sessions
  remain pod-local and die with pod loss; a shared session store (Redis) stays
  a future option if failover behavior becomes a requirement.
- Phase 3 validation (2+ replicas, cross-pod lease contention) is deferred;
  it will be exercised locally on a colima/kind cluster.
- Reads/caches remain replica-local by design (§7, intended).

Phase 1 of the rollout plan (lease-aware actions on one replica) is done.

## Original Design

This document describes how to scale VibeView to multiple concurrent users and multiple
web replicas without introducing a database as a system of record.

The goal is:

- multiple operators can use the app at the same time
- multiple web pods can exist without inconsistent behavior
- operators do not collide on shared operations like drain, evacuate, reboot, or clone
- the app does not rely on a relational database or persistent backend workflow store

This is a concrete design against the current codebase, not a generic architecture note.

## Constraints

- No database-backed application state.
- Browser-only state is acceptable for user-local UI concerns.
- Cluster-wide coordination is required for shared infrastructure actions.
- Kubernetes and OpenStack are already authoritative sources for most real state.

## Current State In This Repo

### In-process user/session state

Today, web sessions are stored in process memory in [session.py](../draino/web/session.py):

- `SessionStore._sessions`
- one `SessionRecord` per authenticated browser session
- each session owns a `DrainoServer`

### In-process server/runtime state

Today, `DrainoServer` in [inventory.py](../draino/web/inventory.py) holds mutable state in memory:

- `node_states`
- OpenStack summary cache
- host signal refresh timestamps
- node detail cache
- node metrics cache
- connected WebSocket clients

This works for one pod, but it is not horizontally safe:

- each web pod sees only its own sessions
- each web pod builds its own view of inventory
- each web pod can launch overlapping actions
- WebSocket updates are only visible to clients on that same pod

### Action execution model

Today, actions are launched from:

- [inventory_actions.py](../draino/web/inventory_actions.py)
- [worker.py](../draino/worker.py)
- workflow modules like:
  - [workflows_maintenance.py](../draino/workflows_maintenance.py)
  - [workflows_evacuate.py](../draino/workflows_evacuate.py)

Those actions are background-thread based and use in-memory state updates plus WebSocket pushes.

### Good news

The app already has an important property that should be preserved:

- most real operational status is derivable from Kubernetes or OpenStack

Examples:

- node cordoned or not
- drain blocked or not
- Nova compute enabled or disabled
- instances still resident on a host
- clone target existence

That makes a no-DB design viable.

## Design Principles

### 1. Web replicas must not be authoritative for shared state

A web pod may cache data and maintain client connections, but it cannot be the only place
where shared operational truth lives.

### 2. Browser owns user-local state

State that affects only one operator experience should stay client-side:

- selected node
- active tabs
- local filters
- local maintenance history
- draft form state

This is already compatible with the current frontend direction.

### 3. Kubernetes and OpenStack remain the authoritative sources of infrastructure truth

The app should derive shared status from those systems whenever possible instead of
inventing a separate workflow state store.

### 4. Shared operations need distributed locking

If two users can click the same action from different replicas, the app must coordinate
that somewhere outside one process.

### 5. Actions must be idempotent

This repo has already moved in that direction. That should continue and become a hard rule.

## Proposed Target Architecture

## Layer 1: Browser-owned UI state

Keep these client-side only:

- maintenance history in `localStorage`
- clone planner form state
- filters and selected rows
- drawer open/close state
- temporary confirmation dialog state

No backend persistence is required for these.

## Layer 2: Stateless request handling in web pods

Web pods should handle:

- auth/session validation
- request routing
- read APIs that fetch/derive current state
- WebSocket fanout to connected clients on that pod

But web pods should not be the sole owner of:

- cluster-wide inventory truth
- long-running action truth
- action exclusivity

## Layer 3: Distributed coordination via Kubernetes Leases

Use Kubernetes `Lease` objects for shared operation locks.

This is the key scaling primitive that avoids a database.

Recommended lock scopes:

- per node for:
  - drain
  - undrain
  - reboot
- per hypervisor/node for:
  - evacuate
  - re-enable Nova
- per instance for:
  - clone

Suggested naming:

- `vibeview-node-<node-name>`
- `vibeview-evacuate-<node-name>`
- `vibeview-clone-<instance-id>`

Suggested lease payload ownership fields:

- holder identity:
  - web pod name
  - session id
  - username
- renew time
- acquire time
- short action label

The Lease is not a workflow database. It is only a coordination object.

## Layer 4: Derived operational status, not stored workflow truth

The UI should treat workflow state as derived from:

- K8s node schedulability
- drain preflight/blockers
- Nova service state
- current hypervisor residents
- recent logs when available

For example:

- `Ready for Maintenance` should remain derived
- `Disabled, Not Evacuated` should remain derived from Nova disabled plus residents
- clone completion should be inferred from the API result or presence of the new server

## What Must Change In This Repo

## 1. Session model

Current issue:

- [session.py](../draino/web/session.py) stores all sessions in-process.

This blocks multi-replica operation unless traffic is pinned to one pod.

Recommended options:

### Option A: Signed cookie sessions

Store session auth payload in an encrypted/signed cookie or split-cookie model.

Pros:

- no external store
- works across replicas

Cons:

- Kubernetes and OpenStack credentials become harder to handle safely
- token/cert payloads may be too large or awkward

### Option B: Shared ephemeral session store

Use Redis only for session storage, not as an application database.

Pros:

- simplest practical multi-replica path
- no need for sticky sessions

Cons:

- introduces one external dependency

Recommendation:

- if strict “no DB” means “no persistent system of record” but allows an ephemeral shared
  store, use Redis for sessions
- if external stores are off the table entirely, use sticky sessions at the ingress and
  accept that sessions remain pod-local

If you want true replica independence, sticky sessions are not enough by themselves, but
they are an acceptable transitional step.

## 2. `DrainoServer` should stop being authoritative

Current issue:

- `DrainoServer.node_states` is the active runtime truth for a session

Target:

- keep `DrainoServer` as a per-connection coordinator and cache holder
- do not rely on it as the only source of shared action state

What can remain in-memory:

- node detail caches
- metrics caches
- local WebSocket clients

What must not remain process-authoritative:

- “who owns this drain”
- “is clone already running”
- “is evacuation already active”

Those become Lease-backed decisions.

## 3. Action entrypoints should acquire leases first

Current issue:

- [inventory_actions.py](../draino/web/inventory_actions.py) can launch actions directly
  after only local checks

Target behavior:

1. derive whether the action is already a no-op
2. if not a no-op, attempt to acquire the corresponding Lease
3. if Lease acquisition fails:
   - return a clear operator-visible “action already running elsewhere” message
4. if acquired:
   - run the action
   - renew the Lease while running if needed
   - release the Lease on completion or failure

This is the main anti-collision mechanism.

## 4. Clone should get a server-side lock

Current issue:

- the clone UI now blocks a second clone from the same browser
- another browser or replica can still submit an overlapping clone

Required change:

- before `POST /api/instances/{id}/clone` runs [clone_instance](../draino/web/resource_helpers.py),
  acquire a per-instance Lease
- reject if already held
- log acquire/release

Expected behavior:

- one source instance can only have one in-flight clone operation cluster-wide
- the frontend lock remains a convenience layer, not the source of truth

## 5. Maintenance actions should get server-side locks

Actions that need Lease protection:

- `drain_quick`
- `undrain`
- `evacuate`
- `enable_nova`
- `reboot`

Recommended scope:

- `drain_quick` / `undrain` / `reboot`:
  per K8s node
- `evacuate` / `enable_nova`:
  per compute node or hypervisor

This prevents:

- one user draining while another undrains
- one pod evacuating while another re-enables Nova
- overlapping reboot and maintenance actions

## 6. Background-thread workflow state should become advisory only

Current issue:

- workflows update `NodeState` in memory and push WebSocket updates

That is acceptable for UI responsiveness on a given pod, but not as the only shared truth.

Target:

- background workflow still emits local logs and local progress
- durable operational correctness comes from:
  - distributed locks
  - idempotent actions
  - real-state reconciliation

This means if a pod dies mid-workflow:

- the lock expires or is released
- a later operator can retry safely
- the UI re-derives the current situation from infrastructure

## 7. Reads should continue using cache tiers, but caches are replica-local

Replica-local caches are fine for:

- OpenStack summaries
- host signal TTLs
- node detail caches
- metrics caches

They do not need to be shared if they are:

- bounded by TTL
- treated as performance helpers
- never treated as authoritative action state

This means horizontal scaling can tolerate duplicated reads better than duplicated writes.

## Recommended Rollout Plan

## Phase 1: Transition-safe multi-user support on one replica

Goal:

- improve correctness first without changing deployment shape

Changes:

- keep one web replica
- add Lease-backed locks anyway
- make all shared operations lock-aware

Why:

- validates the coordination model before replica count increases

## Phase 2: Session strategy decision

Choose one:

### A. Sticky sessions

Use ingress affinity so each browser stays on one pod.

Good for:

- short-term multi-replica deployment

Tradeoff:

- sessions still die with pod loss
- not true shared-session architecture

### B. Shared session store

Use Redis for session records only.

Good for:

- true multi-replica support
- cleaner failover behavior

Recommendation:

- use Redis for sessions if acceptable
- keep application workflow state out of Redis

## Phase 3: Increase replica count

After Lease-backed actions and session strategy are in place:

- run 2+ web replicas
- keep replica-local read caches
- rely on Leases for action exclusivity

## What Should Stay Client-Side

Keep in browser only:

- maintenance history
- clone planner messages
- stale/refresh indicators
- selected project detail
- list sorting and filters
- per-browser UI notes and dismissals

Do not try to make those cluster-global.

## What Should Move To Kubernetes Leases

Move these from implicit process behavior to explicit Leases:

- node maintenance lock
- evacuation lock
- clone lock
- optional reboot lock

If a future action mutates shared infrastructure, assume it needs a Lease unless proven otherwise.

## What In-Memory State Should Stop Being Authoritative First

Highest priority:

1. clone exclusivity
2. node maintenance action exclusivity
3. reboot exclusivity

Second priority:

4. session storage if you need multi-replica without stickiness

Lower priority:

5. replica-local read caches

## Failure Model

Desired behavior under failure:

### Web pod dies during action

- Lease eventually expires
- action may already have partially completed in infrastructure
- next operator retries safely because action is idempotent and state is re-derived

### Two users click the same action from different pods

- first request acquires Lease
- second request gets a clear “already running” response

### User refreshes browser mid-action

- browser-local history may lose some details
- operational truth remains in Kubernetes/OpenStack
- action safety is still preserved by Lease and idempotency

## Non-Goals

This design does not introduce:

- workflow persistence database
- durable per-step historical workflow replay
- cluster-global UI history
- guaranteed exactly-once action semantics

Those are not necessary to achieve safe multi-user operation.

## Recommended Next Implementation Tasks

1. Add a small Kubernetes Lease helper module.
2. Wrap clone API with a per-instance Lease.
3. Wrap maintenance action entrypoints with per-node/per-hypervisor Leases.
4. Decide session strategy:
   - sticky ingress, or
   - Redis session store
5. Update deployment docs to describe the chosen session model and replica-safe operation rules.

## Summary

You do not need a database to scale this app safely.

You do need:

- browser-owned UI state
- idempotent actions
- Kubernetes Lease-based distributed locks
- infrastructure-derived status
- either sticky sessions or a shared ephemeral session store

In this repo, the first things to change are not the caches. The first things to change are
the action entrypoints that currently assume one web process is the only actor.
