'use strict';

let pendingActionConfirm = null;
let actionConfirmTimer = null;
const ACTION_CONFIRM_STALE_MS = 30000;

// ════════════════════════════════════════════════════════════════════════════
// § ACTION BUTTONS
// ════════════════════════════════════════════════════════════════════════════

/** Show/hide and update breadcrumb action buttons based on selected node. */
function updateActionButtons(nd) {
  const nodeActions = document.getElementById('bc-node-actions');
  if (!nd) {
    nodeActions.style.display = 'none';
    return;
  }
  nodeActions.style.display = 'flex';

  const phase   = nd.phase;
  const k8sMaintenanceActive = nd.k8s_drain_status !== 'clear' || nd.k8s_cordoned;
  const k8sDrained = nd.k8s_drain_status === 'drained';
  const evacuated = nd.nova_evacuation_status === 'evacuated';
  const novaDisabledOnly = nd.nova_evacuation_status === 'disabled';
  const rebootReady = !!nd.maintenance_ready;
  const busy    = ['running','rebooting','undraining'].includes(phase);

  const evBtn = document.getElementById('bc-evacuate');
  const enBtn = document.getElementById('bc-enable-nova');
  const drBtn = document.getElementById('bc-drain');
  const rbBtn = document.getElementById('bc-reboot');

  evBtn.textContent = phase === 'running' && nd.is_compute ? '▶ Evacuating…' : evacuated ? '▶ Evacuated' : novaDisabledOnly ? '▶ Resume Evacuation' : '▶ Evacuate';
  evBtn.disabled    = busy || !nd.is_compute;
  evBtn.className   = `btn ${evacuated || novaDisabledOnly ? 'warning' : 'primary'}`;
  evBtn.style.display = hasOpenStackAuth() ? '' : 'none';

  enBtn.textContent = '↺ Re-enable Nova';
  enBtn.disabled = busy || !nd.is_compute || nd.compute_status !== 'disabled';
  enBtn.className = 'btn warning';
  enBtn.style.display = hasOpenStackAuth() && nd.is_compute && nd.compute_status === 'disabled' ? '' : 'none';

  drBtn.textContent = phase === 'undraining' ? '↺ Undraining…' : k8sMaintenanceActive ? '↺ Undrain' : '▽ Drain';
  drBtn.disabled    = busy;
  drBtn.className   = `btn ${k8sMaintenanceActive && !busy ? 'warning' : ''}`;

  rbBtn.textContent = phase === 'rebooting'  ? '⏻ Rebooting…'  : '⏻ Reboot';
  rbBtn.disabled    = busy || !authInfo?.is_admin || !rebootReady;
  rbBtn.title       = !authInfo?.is_admin
    ? "Requires OpenStack admin role"
    : !rebootReady
      ? "Reboot is available only after Kubernetes drain and Nova evacuation are both complete"
      : '';
}

// ════════════════════════════════════════════════════════════════════════════
// § ACTIONS → WEBSOCKET
// ════════════════════════════════════════════════════════════════════════════

function actionRefreshNode() {
  if (!selectedNode) return;
  delete nodeDetailCache[selectedNode];
  delete nodeMetricsCache[selectedNode];
  delete nodeDrainPreflightCache[selectedNode];
  delete nodeEvacuatePreflightCache[selectedNode];
  delete nodeEvacuationResidentsCache[selectedNode];
  loadNodeDetail(selectedNode, true);
  loadNodeDrainPreflight(selectedNode, true);
  if (nodes[selectedNode]?.is_compute) loadNodeEvacuatePreflight(selectedNode, true);
  if (nodes[selectedNode]?.is_compute) loadNodeEvacuationResidents(selectedNode, true);
  if (activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
}

function actionRefreshAll()  {
  Object.keys(nodeDetailCache).forEach((name) => delete nodeDetailCache[name]);
  Object.keys(nodeMetricsCache).forEach((name) => delete nodeMetricsCache[name]);
  Object.keys(nodeDrainPreflightCache).forEach((name) => delete nodeDrainPreflightCache[name]);
  Object.keys(nodeEvacuatePreflightCache).forEach((name) => delete nodeEvacuatePreflightCache[name]);
  Object.keys(nodeEvacuationResidentsCache).forEach((name) => delete nodeEvacuationResidentsCache[name]);
  if (selectedNode) loadNodeDetail(selectedNode, true);
  if (selectedNode) loadNodeDrainPreflight(selectedNode, true);
  if (selectedNode && nodes[selectedNode]?.is_compute) loadNodeEvacuatePreflight(selectedNode, true);
  if (selectedNode && nodes[selectedNode]?.is_compute) loadNodeEvacuationResidents(selectedNode, true);
  if (selectedNode && activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
  setNodeListRefreshing(true);
  wsSend({ action: 'refresh' });
}
async function actionEvacuate() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd?.is_compute) return;
  await loadNodeEvacuatePreflight(selectedNode, true);
  openActionConfirm({
    kind: 'evacuate',
    node: selectedNode,
    title: 'Confirm Nova Evacuation',
    submitLabel: 'Evacuate',
    submitClass: 'btn primary',
    blocked: false,
    bodyHtml: buildEvacuateConfirmHtml(nodeEvacuatePreflightCache[selectedNode]),
  });
}
function actionEnableNova() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd?.is_compute || nd.compute_status !== 'disabled') return;
  openActionConfirm({
    kind: 'enable_nova',
    node: selectedNode,
    title: 'Confirm Nova Re-enable',
    submitLabel: 'Re-enable Nova',
    submitClass: 'btn warning',
    blocked: false,
    requiresFresh: false,
    bodyHtml: buildEnableNovaConfirmHtml(nd),
  });
}
function actionReboot()   { if (selectedNode) wsSend({ action: 'reboot_request', node: selectedNode }); }
async function actionToggleNoSchedule() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd) return;
  const enabled = !hasManagedNoScheduleTaint(nd);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(selectedNode)}/taints/noschedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await resp.json();
    if (!resp.ok || data.ok === false) throw new Error(data.error || `HTTP ${resp.status}`);
    actionRefreshNode();
    wsSend({ action: 'refresh_silent' });
  } catch (err) {
    addLog(selectedNode, `NoSchedule taint error: ${err.message || err}`, 'red');
  }
}

function migrateInstance(instanceId) {
  if (!selectedNode) return;
  const nd   = nodes[selectedNode];
  const inst = nd?.preflight_instances?.find(i => i.id === instanceId);
  instanceMigrateStates[instanceId] = 'migrating';
  instanceMigrateTasks[instanceId]  = {
    name:      inst?.name || instanceId.slice(0, 8),
    nodeName:  selectedNode,
    status:    'migrating',
    startTime: new Date(),
  };
  wsSend({ action: 'migrate_instance', node: selectedNode, instance_id: instanceId });
  if (nd) renderInstancesTab(nd);
  renderTasksPanel();
}

async function actionDrainOrUndrain() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode]; if (!nd) return;
  const action = (nd.k8s_drain_status !== 'clear' || nd.k8s_cordoned) ? 'undrain' : 'drain_quick';
  if (action === 'undrain') {
    wsSend({ action, node: selectedNode });
    return;
  }
  await loadNodeDrainPreflight(selectedNode, true);
  const preview = nodeDrainPreflightCache[selectedNode] || {};
  openActionConfirm({
    kind: 'drain',
    node: selectedNode,
    title: 'Confirm Kubernetes Drain',
    submitLabel: 'Drain',
    submitClass: 'btn primary',
    blocked: Number(preview.summary?.blocking || 0) > 0 || !!preview.error,
    bodyHtml: buildDrainConfirmHtml(preview),
  });
}

function actionPodsInline() {
  if (!selectedNode) return;
  lastPodsCache = null;
  wsSend({ action: 'get_pods', node: selectedNode });
  const sec = document.getElementById('pods-section');
  if (sec) sec.innerHTML = `<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Fetching pods…</div>`;
}

function buildActionStat(label, value) {
  return `<div class="action-confirm-stat"><div class="action-confirm-stat-label">${esc(label)}</div><div class="action-confirm-stat-value">${esc(String(value ?? 0))}</div></div>`;
}

function actionConfirmCacheFor(kind, nodeName) {
  if (!nodeName) return null;
  if (kind === 'enable_nova') return null;
  return kind === 'drain' ? nodeDrainPreflightCache[nodeName] : nodeEvacuatePreflightCache[nodeName];
}

function actionConfirmAgeText(cache) {
  const fetchedAt = cache?.fetchedAt ? new Date(cache.fetchedAt) : null;
  if (!fetchedAt || Number.isNaN(fetchedAt.getTime())) return 'not yet fetched';
  const ageSeconds = Math.max(0, Math.floor((Date.now() - fetchedAt.getTime()) / 1000));
  return ageSeconds < 2 ? 'just now' : `${ageSeconds}s ago`;
}

function actionConfirmIsStale(config, cache) {
  if (config?.requiresFresh === false) return false;
  if (!cache?.fetchedAt) return true;
  if (config?.stateDirty) return true;
  return (Date.now() - new Date(cache.fetchedAt).getTime()) > ACTION_CONFIRM_STALE_MS;
}

function buildDrainConfirmHtml(preview) {
  const evictable = Number(preview.summary?.evictable || 0);
  const skipped = Number(preview.summary?.skipped || 0);
  const blocking = Number(preview.summary?.blocking || 0);
  const blockers = preview.blockers || [];
  const skippedItems = preview.skipped || [];
  const evictableItems = preview.evictable || [];
  return `
    <p>Drain will cordon the Kubernetes node and evict only drain-safe pods. Nova compute state is not touched.</p>
    <div class="action-confirm-grid">
      ${buildActionStat('Evictable', evictable)}
      ${buildActionStat('Skipped', skipped)}
      ${buildActionStat('Blocking', blocking)}
    </div>
    ${preview.error ? `<div class="action-confirm-note block">Preflight failed: ${esc(preview.error)}</div>` : ''}
    ${blocking > 0 ? `<div class="action-confirm-note block">Drain cannot proceed until the blocking workloads below are handled.</div>` : ''}
    ${blocking === 0 && preview.warning ? `<div class="action-confirm-note warn">${esc(preview.warning)}</div>` : ''}
    <div class="action-confirm-section">
      <div class="action-confirm-section-title">Blocking Workloads</div>
      ${blockers.length ? `<ul class="action-confirm-list">${blockers.slice(0, 8).map(item => `<li><strong>${esc(item.ref)}</strong> — ${esc(item.reason || item.owner || '')}</li>`).join('')}</ul>` : '<div class="action-confirm-note">No blocking workloads detected.</div>'}
    </div>
    <div class="action-confirm-section">
      <div class="action-confirm-section-title">Skipped Workloads</div>
      ${skippedItems.length ? `<ul class="action-confirm-list">${skippedItems.slice(0, 6).map(item => `<li><strong>${esc(item.ref)}</strong> — ${esc(item.reason || item.owner || '')}</li>`).join('')}</ul>` : '<div class="action-confirm-note">No skipped workloads.</div>'}
    </div>
    <div class="action-confirm-section">
      <div class="action-confirm-section-title">First Evictions</div>
      ${evictableItems.length ? `<ul class="action-confirm-list">${evictableItems.slice(0, 6).map(item => `<li><strong>${esc(item.ref)}</strong> — ${esc(item.owner || '')}</li>`).join('')}</ul>` : '<div class="action-confirm-note">Nothing to evict.</div>'}
    </div>
  `;
}

function buildEvacuateConfirmHtml(preview) {
  const items = preview.items || [];
  const amphora = Number(preview.summary?.amphora || 0);
  const ephemeral = Number(preview.summary?.ephemeral || 0);
  const nd = selectedNode ? nodes[selectedNode] : null;
  return `
    <p>Evacuate will disable the Nova compute service and move instances off this hypervisor. Kubernetes drain state is not touched.</p>
    ${nd?.compute_status === 'disabled' ? `<div class="action-confirm-note warn">Nova is already disabled on this host. This action will continue evacuation against the remaining instances.</div>` : ''}
    <div class="action-confirm-grid">
      ${buildActionStat('Instances', preview.summary?.instances || 0)}
      ${buildActionStat('Regular VMs', preview.summary?.regular_vms || 0)}
      ${buildActionStat('Amphora', amphora)}
    </div>
    <div class="action-confirm-grid">
      ${buildActionStat('Volume-backed', preview.summary?.volume_backed || 0)}
      ${buildActionStat('Ephemeral', ephemeral)}
      ${buildActionStat('Active', preview.summary?.active || 0)}
    </div>
    ${preview.error ? `<div class="action-confirm-note block">Preflight failed: ${esc(preview.error)}</div>` : ''}
    ${preview.warning ? `<div class="action-confirm-note ${amphora > 0 || ephemeral > 0 ? 'warn' : ''}">${esc(preview.warning)}</div>` : ''}
    <div class="action-confirm-section">
      <div class="action-confirm-section-title">Sample Instances</div>
      ${items.length ? `<ul class="action-confirm-list">${items.slice(0, 8).map(item => `<li><strong>${esc(item.name || item.id || '')}</strong> — ${esc(item.status || 'unknown')}${item.is_amphora ? ' · Amphora' : item.is_volume_backed ? ' · volume-backed' : ' · ephemeral'}</li>`).join('')}</ul>` : '<div class="action-confirm-note">No instances are currently on this host.</div>'}
    </div>
  `;
}

function buildEnableNovaConfirmHtml(nd) {
  const residentSummary = (nodeEvacuationResidentsCache[nd.k8s_name] || {}).summary || {};
  const residents = residentSummary.instances ?? nd.vm_count ?? 0;
  const amphora = residentSummary.amphora ?? nd.amphora_count ?? 0;
  const failed = residentSummary.failed || 0;
  return `
    <p>Re-enable Nova will allow the scheduler to place instances on this host again. Kubernetes drain state is not touched.</p>
    <div class="action-confirm-grid">
      ${buildActionStat('Current residents', residents)}
      ${buildActionStat('Amphora', amphora)}
      ${buildActionStat('Failed migrations', failed)}
    </div>
    <div class="action-confirm-note warn">Use this when you want to back out of maintenance or pause evacuation. Any current residents will remain on the host.</div>
    ${nd.nova_evacuation_detail ? `<div class="action-confirm-note">${esc(nd.nova_evacuation_detail)}</div>` : ''}
  `;
}

function openActionConfirm(config) {
  pendingActionConfirm = { ...config, openedAt: Date.now(), stateDirty: false };
  document.getElementById('action-confirm-title').textContent = config.title || 'Confirm Action';
  document.getElementById('action-confirm-node').textContent = config.node || '';
  const submit = document.getElementById('action-confirm-submit');
  submit.textContent = pendingActionConfirm.submitLabel || 'Continue';
  submit.className = pendingActionConfirm.submitClass || 'btn primary';
  renderActionConfirm();
  document.getElementById('action-confirm-overlay').classList.add('open');
  if (actionConfirmTimer) clearInterval(actionConfirmTimer);
  actionConfirmTimer = setInterval(renderActionConfirm, 1000);
}

function closeActionConfirm() {
  pendingActionConfirm = null;
  if (actionConfirmTimer) clearInterval(actionConfirmTimer);
  actionConfirmTimer = null;
  document.getElementById('action-confirm-overlay').classList.remove('open');
}

function renderActionConfirm() {
  if (!pendingActionConfirm) return;
  const cache = actionConfirmCacheFor(pendingActionConfirm.kind, pendingActionConfirm.node);
  const stale = actionConfirmIsStale(pendingActionConfirm, cache);
  const changed = !!pendingActionConfirm.stateDirty;
  const requiresFresh = pendingActionConfirm.requiresFresh !== false;
  const statusTone = changed ? 'block' : stale ? 'warn' : '';
  const statusText = !requiresFresh
    ? 'This action applies immediately and does not require a preflight refresh.'
    : changed
      ? 'Node state changed while this dialog was open. Refresh preflight before continuing.'
      : stale
        ? 'Preflight is stale. Refresh before continuing.'
        : `Preflight last checked ${actionConfirmAgeText(cache)}.`;
  const body = `
    <div class="action-confirm-toolbar">
      <div class="action-confirm-note ${statusTone}">${esc(statusText)}</div>
      ${requiresFresh ? '<button class="btn" type="button" onclick="refreshActionConfirm()">↺ Refresh Preflight</button>' : ''}
    </div>
    ${pendingActionConfirm.bodyHtml || ''}
  `;
  document.getElementById('action-confirm-body').innerHTML = body;
  const submit = document.getElementById('action-confirm-submit');
  const blocked = !!pendingActionConfirm.blocked || stale || changed;
  submit.disabled = blocked;
  submit.title = pendingActionConfirm.blocked
    ? 'Resolve the blocking conditions before continuing.'
    : (stale || changed)
      ? 'Refresh preflight before continuing.'
      : '';
}

async function refreshActionConfirm() {
  if (!pendingActionConfirm) return;
  const { kind, node } = pendingActionConfirm;
  if (kind === 'drain') {
    await loadNodeDrainPreflight(node, true);
    const preview = nodeDrainPreflightCache[node] || {};
    pendingActionConfirm.bodyHtml = buildDrainConfirmHtml(preview);
    pendingActionConfirm.blocked = Number(preview.summary?.blocking || 0) > 0 || !!preview.error;
  } else if (kind === 'evacuate') {
    await loadNodeEvacuatePreflight(node, true);
    const preview = nodeEvacuatePreflightCache[node] || {};
    pendingActionConfirm.bodyHtml = buildEvacuateConfirmHtml(preview);
    pendingActionConfirm.blocked = false;
  } else if (kind === 'enable_nova') {
    pendingActionConfirm.bodyHtml = buildEnableNovaConfirmHtml(nodes[node] || {});
    pendingActionConfirm.blocked = false;
  }
  pendingActionConfirm.stateDirty = false;
  renderActionConfirm();
}

function markActionConfirmDirty(nodeName) {
  if (!pendingActionConfirm || pendingActionConfirm.node !== nodeName) return;
  pendingActionConfirm.stateDirty = true;
  renderActionConfirm();
}

function confirmAction() {
  if (!pendingActionConfirm) return;
  const { kind, node } = pendingActionConfirm;
  closeActionConfirm();
  if (kind === 'drain') wsSend({ action: 'drain_quick', node });
  if (kind === 'evacuate') wsSend({ action: 'evacuate', node });
  if (kind === 'enable_nova') wsSend({ action: 'enable_nova', node });
}

// ════════════════════════════════════════════════════════════════════════════
// § MODALS
// ════════════════════════════════════════════════════════════════════════════

function cancelReboot() {
  document.getElementById('modal-overlay').classList.remove('open');
  if (pendingReboot) { wsSend({ action: 'reboot_cancel', node: pendingReboot }); pendingReboot = null; }
}

function confirmReboot() {
  if (document.getElementById('modal-input').value.trim() !== 'YES') {
    const inp = document.getElementById('modal-input');
    inp.classList.remove('shake'); void inp.offsetWidth; inp.classList.add('shake'); inp.focus();
    return;
  }
  document.getElementById('modal-overlay').classList.remove('open');
  if (pendingReboot) { wsSend({ action: 'reboot_confirm', node: pendingReboot }); pendingReboot = null; }
}

function closeBlocked() { document.getElementById('blocked-overlay').classList.remove('open'); }
