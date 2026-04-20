'use strict';

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
  const rebootReady = !!nd.maintenance_ready;
  const busy    = ['running','rebooting','undraining'].includes(phase);

  const evBtn = document.getElementById('bc-evacuate');
  const drBtn = document.getElementById('bc-drain');
  const rbBtn = document.getElementById('bc-reboot');

  evBtn.textContent = phase === 'running' && nd.is_compute ? '▶ Evacuating…' : evacuated ? '▶ Evacuated' : '▶ Evacuate';
  evBtn.disabled    = busy || !nd.is_compute;
  evBtn.className   = `btn ${evacuated && !busy ? 'warning' : 'primary'}`;
  evBtn.style.display = hasOpenStackAuth() ? '' : 'none';

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
  loadNodeDetail(selectedNode, true);
  loadNodeDrainPreflight(selectedNode, true);
  if (nodes[selectedNode]?.is_compute) loadNodeEvacuatePreflight(selectedNode, true);
  if (activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
}

function actionRefreshAll()  {
  Object.keys(nodeDetailCache).forEach((name) => delete nodeDetailCache[name]);
  Object.keys(nodeMetricsCache).forEach((name) => delete nodeMetricsCache[name]);
  Object.keys(nodeDrainPreflightCache).forEach((name) => delete nodeDrainPreflightCache[name]);
  Object.keys(nodeEvacuatePreflightCache).forEach((name) => delete nodeEvacuatePreflightCache[name]);
  if (selectedNode) loadNodeDetail(selectedNode, true);
  if (selectedNode) loadNodeDrainPreflight(selectedNode, true);
  if (selectedNode && nodes[selectedNode]?.is_compute) loadNodeEvacuatePreflight(selectedNode, true);
  if (selectedNode && activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
  setNodeListRefreshing(true);
  wsSend({ action: 'refresh' });
}
function actionEvacuate() { if (selectedNode) wsSend({ action: 'evacuate',       node: selectedNode }); }
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

function actionDrainOrUndrain() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode]; if (!nd) return;
  const action = (nd.k8s_drain_status !== 'clear' || nd.k8s_cordoned) ? 'undrain' : 'drain_quick';
  wsSend({ action, node: selectedNode });
}

function actionPodsInline() {
  if (!selectedNode) return;
  lastPodsCache = null;
  wsSend({ action: 'get_pods', node: selectedNode });
  const sec = document.getElementById('pods-section');
  if (sec) sec.innerHTML = `<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Fetching pods…</div>`;
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
