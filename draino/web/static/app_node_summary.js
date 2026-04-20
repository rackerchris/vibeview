// ════════════════════════════════════════════════════════════════════════════
// § SUMMARY TAB
// ════════════════════════════════════════════════════════════════════════════

function renderSummaryTab(nd) {
  let h = '';
  const det = nodeDetailCache[nd.k8s_name] || {};
  const detLoading = det.loading;
  const k8sDrainStatus = nd.k8s_drain_status || 'clear';
  const novaEvacStatus = nd.nova_evacuation_status || 'clear';
  const maintenanceGuidance = getNodeMaintenanceGuidance(nd);
  const maintenanceSteps = uniqueGuidanceLines(maintenanceGuidance.steps);
  const maintenanceLabel = nd.maintenance_ready
    ? 'Ready for Maintenance'
    : k8sDrainStatus !== 'drained'
      ? 'Not Ready: Kubernetes drain incomplete'
      : nd.is_compute && novaEvacStatus === 'disabled'
        ? 'Not Ready: Nova still hosting instances'
        : nd.is_compute && novaEvacStatus !== 'evacuated'
          ? 'Not Ready: Nova evacuation incomplete'
        : 'Not Ready for Maintenance';
  const drainPreflight = nodeDrainPreflightCache[nd.k8s_name] || { loading: true, summary: {}, evictable: [], skipped: [], blockers: [] };
  const evacuatePreflight = nodeEvacuatePreflightCache[nd.k8s_name] || { loading: nd.is_compute, summary: {}, items: [] };
  const evacuationResidents = nodeEvacuationResidentsCache[nd.k8s_name] || { loading: false, summary: {}, items: [] };

  function preflightCountLine(label, value, tone = '') {
    return `<div class="mrow"><span class="ml">${esc(label)}</span><span class="mv ${tone}">${esc(String(value ?? 0))}</span></div>`;
  }

  function preflightPodList(title, items, emptyLabel, tone = '') {
    if (!items?.length) {
      return `<div class="mrow"><span class="ml">${esc(title)}</span><span class="mv">${esc(emptyLabel)}</span></div>`;
    }
    const names = items.slice(0, 3).map(item => `${item.namespace}/${item.name}`).join(', ');
    const more = items.length > 3 ? ` +${items.length - 3} more` : '';
    return `<div class="mrow"><span class="ml">${esc(title)}</span><span class="mv ${tone}" title="${esc(items.map(item => `${item.ref} (${item.owner})`).join('\n'))}">${esc(names + more)}</span></div>`;
  }

  function novaEvacTone(status) {
    if (status === 'evacuated') return 'green';
    if (status === 'blocked' || status === 'error') return 'red';
    if (status === 'in_progress' || status === 'disabled') return 'yellow';
    return '';
  }

  function novaEvacLabel(status) {
    if (status === 'disabled') return 'disabled, not evacuated';
    return String(status || 'clear').replace('_', ' ');
  }

  h += `<div class="concept-map">
    <strong>💡 VMware → OpenStack/K8s quick reference:</strong>
    <div class="cm-grid">
      <span class="cm-from">ESXi Host</span><span class="cm-arrow">→</span><span class="cm-to">Nova Hypervisor + K8s Node</span>
      <span class="cm-from">Drain</span><span class="cm-arrow">→</span><span class="cm-to">Cordon + drain Kubernetes pods</span>
      <span class="cm-from">Evacuate</span><span class="cm-arrow">→</span><span class="cm-to">Disable Nova + evacuate instances</span>
      <span class="cm-from">vMotion</span><span class="cm-arrow">→</span><span class="cm-to">Nova Live Migration</span>
      <span class="cm-from">VM</span><span class="cm-arrow">→</span><span class="cm-to">Nova Instance (QEMU/KVM) or K8s Pod</span>
    </div>
    <div style="margin-top:5px;font-size:10px">
      <a href="#" onclick="showTab('configure');return false" style="color:var(--blue)">Full VMware reference →</a>
    </div>
  </div>`;

  if (detLoading) {
    h += `<div class="runtime-note" style="margin-bottom:10px"><span class="spinner">⟳</span> Node details are still refreshing. Inventory status is loaded, but hardware, Nova, and Kubernetes detail may still be filling in.</div>`;
  }

  if (nd.is_etcd) {
    const peers = Object.values(nodes).filter(n => n.is_etcd);
    const total = peers.length;
    const quorum = Math.floor(total / 2) + 1;
    if (peers.some(n => n.etcd_checking)) {
      h += `<div class="etcd-alert checking"><span class="spinner">⟳</span> Checking etcd health on all ${total} etcd nodes…</div>`;
    } else {
      const checked = peers.filter(n => n.etcd_healthy !== null && n.etcd_healthy !== undefined);
      if (!checked.length) {
        const error = String(nd.etcd_error || '').toLowerCase();
        const detail = !error
          ? 'health unknown (checked before reboot)'
          : ['permission', 'forbidden', 'unauthorized', 'denied', '403'].some(token => error.includes(token))
            ? 'service status could not be validated because node-agent permissions are insufficient'
            : ['node-agent', 'connection refused', 'timed out', 'timeout', '404', '502', '503', '504', 'no route', 'unreachable', 'not found', 'ssl'].some(token => error.includes(token))
              ? 'service status could not be validated because the node-agent is inaccessible'
              : 'service status could not be validated';
        h += `<div class="etcd-alert">⚠ etcd node — ${esc(detail)}</div>`;
      } else {
        const healthy = peers.filter(n => n.etcd_healthy === true).length;
        const remaining = healthy - (nd.etcd_healthy === true ? 1 : 0);
        const atRisk = remaining < quorum;
        const peerList = peers.map(p =>
          `${esc(p.k8s_name)} ${p.etcd_healthy===true?'✓':p.etcd_healthy===false?'✗':'?'}`
        ).join('  ·  ');
        h += `<div class="etcd-alert ${atRisk?'danger':''}">
          ${atRisk
            ? `⚠ ETCD QUORUM RISK — ${healthy}/${total} healthy, reboot would leave ${remaining} (need ${quorum})`
            : `⚠ etcd — ${healthy}/${total} healthy · safe to work on ${total - quorum} at a time`}
          <div class="etcd-peers">${peerList}</div>
        </div>`;
      }
    }
  }

  if (nd.phase === 'rebooting' && nd.reboot_start) {
    const elapsed = Math.floor(Date.now() / 1000 - nd.reboot_start);
    h += `<div class="downtime-counter">⏱ Downtime: ${elapsed}s</div>`;
  }

  if (nd.phase === 'idle' && nd.steps?.length) {
    if (nd.reboot_downtime != null)
      h += `<div class="reboot-complete">✓ Reboot complete — total downtime: ${Math.round(nd.reboot_downtime)}s</div>`;
    if (k8sDrainStatus !== 'clear' || nd.k8s_cordoned)
      h += `<div class="idle-hint">Kubernetes drain posture is active — click <strong>Drain</strong> again to uncordon.</div>`;
  }

  h += `<div class="runtime-note" style="margin-bottom:10px">${esc(maintenanceLabel)}</div>`;
  h += `<div class="maintenance-guidance maintenance-guidance-${escAttr(maintenanceGuidance.tone)}" style="margin-bottom:12px">
    <div class="maintenance-guidance-title">${esc(maintenanceGuidance.title)}</div>
    <div class="maintenance-guidance-summary">${esc(maintenanceGuidance.summary)}</div>
    ${maintenanceSteps.length ? `<div class="maintenance-guidance-steps">${maintenanceSteps.map((step) => `<div class="maintenance-guidance-step">${esc(step)}</div>`).join('')}</div>` : ''}
  </div>`;

  const k8sd = det.k8s || {};
  const novd = det.nova || {};
  const hwd = det.hw || {};

  h += `<div class="summary-grid">`;

  h += `<div class="card">
    <div class="card-title">Drain Preflight</div>
    <div class="card-body">
      ${drainPreflight.loading ? `<div class="runtime-note"><span class="spinner">⟳</span> Checking Kubernetes drain eligibility…</div>` : ''}
      ${drainPreflight.error ? `<div class="err-chip">⚠ ${esc(drainPreflight.error)}</div>` : ''}
      ${!drainPreflight.loading && !drainPreflight.error ? `
        ${preflightCountLine('Evictable pods', drainPreflight.summary?.evictable || 0)}
        ${preflightCountLine('DaemonSets skipped', drainPreflight.summary?.skipped || 0)}
        ${preflightCountLine('Blocking pods', drainPreflight.summary?.blocking || 0, (drainPreflight.summary?.blocking || 0) > 0 ? 'red' : '')}
        ${preflightPodList('Blockers', drainPreflight.blockers, 'none', (drainPreflight.blockers || []).length ? 'red' : '')}
        ${preflightPodList('Skipped', drainPreflight.skipped, 'none')}
        ${preflightPodList('First evictions', drainPreflight.evictable, 'none')}
        ${drainPreflight.warning ? `<div class="runtime-note" style="margin-top:8px">${esc(drainPreflight.warning)}</div>` : ''}
      ` : ''}
    </div>
  </div>`;

  if (nd.is_compute) {
    const nc = { up:'green', disabled:'yellow', down:'red' }[nd.compute_status] || 'gray';
    const nl = { up:'up · enabled', disabled:'disabled', down:'DOWN' }[nd.compute_status] || '…';
    const v = nd.vm_count != null ? nd.vm_count : '…';
    const a = nd.amphora_count != null ? nd.amphora_count : '…';
    const vcpuUsed = novd.vcpus_used;
    const vcpuTotal = novd.vcpus;
    const ramUsed = novd.memory_mb_used;
    const ramTotal = novd.memory_mb;
    const vcpuPct = vcpuTotal ? Math.round(vcpuUsed / vcpuTotal * 100) : null;
    const ramPct = ramTotal ? Math.round(ramUsed / ramTotal * 100) : null;
    const vcpuCls = vcpuPct >= 90 ? 'crit' : vcpuPct >= 70 ? 'warn' : '';
    const ramCls = ramPct >= 90 ? 'crit' : ramPct >= 70 ? 'warn' : '';
    h += `<div class="card">
      <div class="card-title">Nova Compute <span class="hint">VMware Cluster</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Service state</span><span class="mv ${nc}">${esc(nl)}</span></div>
        <div class="mrow"><span class="ml">Evacuation</span><span class="mv ${novaEvacTone(novaEvacStatus)}">${esc(novaEvacLabel(novaEvacStatus))}</span></div>
        <div class="mrow"><span class="ml">Instances <span class="hint">VMs</span></span><span class="mv">${v}</span></div>
        <div class="mrow"><span class="ml">Amphora LBs <span class="hint">NSX LB</span></span><span class="mv ${a > 0 ? 'yellow' : ''}">${a}</span></div>
        ${nd.nova_evacuation_detail ? `<div class="runtime-note" style="margin-top:8px">${esc(nd.nova_evacuation_detail)}</div>` : ''}
        ${vcpuTotal != null ? `<div class="mrow"><span class="ml">vCPUs</span><span class="mv">${vcpuUsed ?? '…'} / ${vcpuTotal}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">vCPUs</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${ramTotal != null ? `<div class="mrow"><span class="ml">RAM</span><span class="mv">${ramUsed != null ? Math.round(ramUsed/1024) : '…'} / ${Math.round(ramTotal/1024)} GB</span></div>` : ''}
        ${vcpuTotal != null && vcpuPct != null ? `<div class="pbw"><div class="pb-label"><span>vCPU allocation</span><span>${vcpuPct}%</span></div><div class="pb-track"><div class="pb-fill ${vcpuCls}" style="width:${vcpuPct}%"></div></div></div>` : ''}
        ${ramTotal != null && ramPct != null ? `<div class="pbw"><div class="pb-label"><span>RAM allocation</span><span>${ramPct}%</span></div><div class="pb-track"><div class="pb-fill ${ramCls}" style="width:${ramPct}%"></div></div></div>` : ''}
      </div>
    </div>`;
  }

  if (nd.is_compute) {
    h += `<div class="card">
      <div class="card-title">Evacuation Preflight</div>
      <div class="card-body">
        ${evacuatePreflight.loading ? `<div class="runtime-note"><span class="spinner">⟳</span> Checking Nova evacuation scope…</div>` : ''}
        ${evacuatePreflight.error ? `<div class="err-chip">⚠ ${esc(evacuatePreflight.error)}</div>` : ''}
        ${!evacuatePreflight.loading && !evacuatePreflight.error ? `
          ${preflightCountLine('Instances on host', evacuatePreflight.summary?.instances || 0)}
          ${preflightCountLine('Regular VMs', evacuatePreflight.summary?.regular_vms || 0)}
          ${preflightCountLine('Amphora', evacuatePreflight.summary?.amphora || 0, (evacuatePreflight.summary?.amphora || 0) > 0 ? 'yellow' : '')}
          ${preflightCountLine('Volume-backed', evacuatePreflight.summary?.volume_backed || 0)}
          ${preflightCountLine('Ephemeral', evacuatePreflight.summary?.ephemeral || 0, (evacuatePreflight.summary?.ephemeral || 0) > 0 ? 'yellow' : '')}
          ${preflightCountLine('Active', evacuatePreflight.summary?.active || 0)}
          ${evacuatePreflight.items?.length ? `<div class="mrow"><span class="ml">Sample instances</span><span class="mv" title="${esc(evacuatePreflight.items.map(item => `${item.name} (${item.status})`).join('\n'))}">${esc(evacuatePreflight.items.slice(0, 3).map(item => item.name).join(', '))}${evacuatePreflight.items.length > 3 ? esc(` +${evacuatePreflight.items.length - 3} more`) : ''}</span></div>` : `<div class="mrow"><span class="ml">Sample instances</span><span class="mv">none</span></div>`}
          ${evacuatePreflight.warning ? `<div class="runtime-note" style="margin-top:8px">${esc(evacuatePreflight.warning)}</div>` : ''}
        ` : ''}
      </div>
    </div>`;
  }

  if (nd.is_compute && (novaEvacStatus === 'disabled' || novaEvacStatus === 'blocked')) {
    h += `<div class="card">
      <div class="card-title">Current Host Residents</div>
      <div class="card-body">
        ${evacuationResidents.loading ? `<div class="runtime-note"><span class="spinner">⟳</span> Checking current host residents…</div>` : ''}
        ${evacuationResidents.error ? `<div class="err-chip">⚠ ${esc(evacuationResidents.error)}</div>` : ''}
        ${!evacuationResidents.loading && !evacuationResidents.error ? `
          ${preflightCountLine('Instances still present', evacuationResidents.summary?.instances || 0, (evacuationResidents.summary?.instances || 0) > 0 ? 'yellow' : '')}
          ${preflightCountLine('Migration failed', evacuationResidents.summary?.failed || 0, (evacuationResidents.summary?.failed || 0) > 0 ? 'red' : '')}
          ${preflightCountLine('Amphora remaining', evacuationResidents.summary?.amphora || 0, (evacuationResidents.summary?.amphora || 0) > 0 ? 'yellow' : '')}
          ${evacuationResidents.items?.length ? `<div class="mrow"><span class="ml">Current names</span><span class="mv" title="${esc(evacuationResidents.items.map(item => `${item.name} (${item.status})`).join('\n'))}">${esc(evacuationResidents.items.slice(0, 4).map(item => item.name).join(', '))}${evacuationResidents.items.length > 4 ? esc(` +${evacuationResidents.items.length - 4} more`) : ''}</span></div>` : `<div class="mrow"><span class="ml">Current names</span><span class="mv">none</span></div>`}
          ${evacuationResidents.warning ? `<div class="runtime-note" style="margin-top:8px">${esc(evacuationResidents.warning)}</div>` : ''}
        ` : ''}
      </div>
    </div>`;
  }

  {
    const rc = nd.k8s_cordoned ? 'gray' : nd.k8s_ready ? 'green' : 'red';
    const rl = nd.k8s_cordoned ? 'Cordoned' : nd.k8s_ready ? 'Ready' : 'Not Ready';
    const podCount = k8sd.pod_count;
    const podCap = k8sd.pods_allocatable ? parseInt(k8sd.pods_allocatable) : null;
    const podPct = podCount != null && podCap ? Math.round(podCount / podCap * 100) : null;
    const podCls = podPct >= 90 ? 'crit' : podPct >= 70 ? 'warn' : '';
    const roles = (k8sd.roles || []).join(', ') || '—';
    h += `<div class="card">
      <div class="card-title">Kubernetes Node <span class="hint">ESXi Host</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Status</span><span class="mv ${rc}">${rl}</span></div>
        <div class="mrow"><span class="ml">Cordoned <span class="hint">Scheduling blocked</span></span><span class="mv ${nd.k8s_cordoned?'yellow':'green'}">${nd.k8s_cordoned?'Yes':'No'}</span></div>
        <div class="mrow"><span class="ml">Drain</span><span class="mv ${k8sDrainStatus === 'drained' ? 'green' : k8sDrainStatus === 'blocked' ? 'red' : k8sDrainStatus === 'in_progress' ? 'yellow' : ''}">${esc(k8sDrainStatus.replace('_', ' '))}</span></div>
        <div class="mrow"><span class="ml">Role</span><span class="mv">${esc(roles)}</span></div>
        ${nd.k8s_drain_detail ? `<div class="runtime-note" style="margin-top:8px">${esc(nd.k8s_drain_detail)}</div>` : ''}
        ${nd.is_etcd ? `<div class="mrow"><span class="ml">etcd</span><span class="mv red">member</span></div>` : ''}
        ${podCount != null ? `<div class="mrow"><span class="ml">Running pods</span><span class="mv">${podCount}${podCap ? ' / ' + podCap : ''}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">Pods</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${k8sd.kubelet_version ? `<div class="mrow"><span class="ml">Kubelet</span><span class="mv dim">${esc(k8sd.kubelet_version)}</span></div>` : ''}
        ${k8sd.container_runtime ? `<div class="mrow"><span class="ml">Runtime</span><span class="mv dim">${esc(k8sd.container_runtime)}</span></div>` : ''}
        ${podPct != null ? `<div class="pbw"><div class="pb-label"><span>Pod capacity</span><span>${podPct}%</span></div><div class="pb-track"><div class="pb-fill ${podCls}" style="width:${podPct}%"></div></div></div>` : ''}
        ${k8sd.error ? `<div class="err-chip" title="${esc(k8sd.error)}">⚠ K8s API: ${esc(k8sd.error.length > 60 ? k8sd.error.slice(0,60)+'…' : k8sd.error)}</div>` : ''}
      </div>
    </div>`;
  }

  {
    const vendor = hwd.vendor || null;
    const product = hwd.product || null;
    const chassis = (vendor && product) ? `${vendor} ${product}` : (vendor || product || null);

    const cpuInfo = novd.cpu_info || {};
    const novaTopo = cpuInfo.topology || {};
    const cpuModel = hwd.cpu_model || cpuInfo.model || cpuInfo.vendor || null;
    const sockets = hwd.cpu_sockets || novaTopo.sockets || null;
    const coresPerS = hwd.cpu_cores_per_socket || novaTopo.cores || null;
    const threadsPerC = hwd.cpu_threads_per_core || (novaTopo.threads > 1 ? novaTopo.threads : null);
    const totalCores = sockets && coresPerS ? sockets * coresPerS : null;
    const totalThreads = totalCores && threadsPerC ? totalCores * threadsPerC : null;

    const ramGb = hwd.ram_total_gb || (k8sd.memory_capacity_kb ? Math.round(k8sd.memory_capacity_kb / 1024 / 1024) : null);
    const ramType = hwd.ram_type || null;
    const ramSpeed = hwd.ram_speed || null;
    const ramMfr = hwd.ram_manufacturer || null;
    const ramSlots = hwd.ram_slots_used || null;
    let ramStr = ramGb ? `${ramGb} GB` : null;
    if (ramStr && ramType) ramStr += ` ${ramType}`;
    if (ramStr && ramSpeed) ramStr += ` @ ${ramSpeed}`;

    h += `<div class="card">
      <div class="card-title">Hardware / Host Info</div>
      <div class="card-body">
        ${chassis ? `<div class="mrow"><span class="ml">System</span><span class="mv" style="font-size:11px">${esc(chassis)}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">System</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${hwd.bios_version ? `<div class="mrow"><span class="ml">BIOS</span><span class="mv dim" style="font-size:10px">${esc(hwd.bios_version)}</span></div>` : ''}
        ${cpuModel ? `<div class="mrow"><span class="ml">CPU</span><span class="mv" style="font-size:11px">${esc(cpuModel)}</span></div>` : detLoading && !chassis ? '' : ''}
        ${totalCores ? `<div class="mrow"><span class="ml">CPU topology</span><span class="mv">${sockets}s × ${coresPerS}c${threadsPerC > 1 ? ' × ' + threadsPerC + 't' : ''} = ${totalThreads || totalCores} logical CPUs</span></div>` : coresPerS ? `<div class="mrow"><span class="ml">CPU cores</span><span class="mv">${coresPerS}</span></div>` : ''}
        ${novd.vcpus ? `<div class="mrow"><span class="ml">vCPUs (Nova)</span><span class="mv">${novd.vcpus}</span></div>` : ''}
        ${ramStr ? `<div class="mrow"><span class="ml">RAM</span><span class="mv">${esc(ramStr)}${ramSlots ? `<span style="color:var(--dim);font-size:10px"> (${ramSlots} DIMMs${ramMfr ? ', ' + ramMfr : ''})</span>` : ''}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">RAM</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${k8sd.os_image ? `<div class="mrow"><span class="ml">OS</span><span class="mv dim" style="font-size:10px">${esc(k8sd.os_image)}</span></div>` : ''}
        ${hwd.kernel_version || nd.kernel_version || k8sd.kernel_version ? `<div class="mrow"><span class="ml">Kernel</span><span class="mv dim" style="font-size:10px">${esc(hwd.kernel_version || nd.kernel_version || k8sd.kernel_version)}</span></div>` : ''}
        ${hwd.architecture || k8sd.architecture ? `<div class="mrow"><span class="ml">Architecture</span><span class="mv">${esc(hwd.architecture || k8sd.architecture)}</span></div>` : ''}
        ${hwd.uptime || nd.uptime ? `<div class="mrow"><span class="ml">Uptime</span><span class="mv">${esc(hwd.uptime || nd.uptime)}</span></div>` : ''}
        ${hwd.error ? `<div class="err-chip" title="${esc(hwd.error)}">⚠ Host detail: ${esc(hwd.error.length > 60 ? hwd.error.slice(0,60)+'…' : hwd.error)}</div>` : ''}
      </div>
    </div>`;
  }

  if (nd.is_compute && (nd.availability_zone || nd.aggregates?.length)) {
    h += `<div class="card">
      <div class="card-title">Placement</div>
      <div class="card-body">
        ${nd.availability_zone ? `<div class="mrow"><span class="ml">Availability Zone</span><span class="mv">${esc(nd.availability_zone)}</span></div>` : ''}
        ${nd.hypervisor ? `<div class="mrow"><span class="ml">Hypervisor</span><span class="mv dim" style="font-size:10px">${esc(nd.hypervisor)}</span></div>` : ''}
        ${nd.aggregates?.length ? `<div class="mrow"><span class="ml">Aggregates</span><span class="mv" style="font-size:10px">${nd.aggregates.map(a => `<span class="tree-badge agg">${esc(a)}</span>`).join(' ')}</span></div>` : ''}
      </div>
    </div>`;
  }

  h += `</div>`;

  const labels = Object.entries(k8sd.labels || {});
  const annotations = Object.entries(k8sd.annotations || {});
  const renderKvRows = (items, emptyLabel) => {
    if (!items.length) {
      return `<div style="color:var(--dim);font-size:12px">${emptyLabel}</div>`;
    }
    return `<div style="max-height:240px;overflow:auto;border:1px solid #eef2f5;border-radius:3px">
      <table class="data-table" style="margin:0">
        <tbody>
          ${items.map(([key, value]) => `<tr>
            <td style="width:42%;font-family:monospace;font-size:10px;vertical-align:top">${esc(key)}</td>
            <td style="font-size:10px;vertical-align:top;word-break:break-all">${esc(value)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  };

  h += `<div class="summary-grid">`;
  h += `<div class="card">
    <div class="card-title">Node Labels</div>
    <div class="card-body">
      ${renderKvRows(labels, 'No labels reported.')}
    </div>
  </div>`;
  h += `<div class="card">
    <div class="card-title">Node Annotations</div>
    <div class="card-body">
      ${renderKvRows(annotations, 'No annotations reported.')}
    </div>
  </div>`;
  h += `</div>`;

  if (nd.is_compute && nd.phase === 'idle' && (!nd.steps || !nd.steps.length)) {
    h += hasOpenStackAuth()
      ? `<div class="idle-hint">Click <strong>Evacuate</strong> to disable Nova and evacuate instances from this hypervisor. Use <strong>Drain</strong> separately for Kubernetes workloads.</div>`
      : `<div class="idle-hint">OpenStack credentials are required for instance evacuation workflows. Use the <strong>Instances</strong> and <strong>Pods</strong> tabs to inspect workloads on this hypervisor.</div>`;
    if (k8sDrainStatus !== 'clear' || nd.k8s_cordoned)
      h += `<div class="idle-hint">Kubernetes drain posture is active — click <strong>Drain</strong> again to uncordon.</div>`;
  }

  if (!nd.is_compute && nd.phase === 'idle' && (!nd.steps || !nd.steps.length)) {
    h += `<div style="color:var(--dim);font-size:12px;padding:4px 0">Non-compute node — no OpenStack evacuation required.</div>`;
    if (nd.k8s_cordoned)
      h += `<div class="idle-hint">Node is cordoned — click <strong>Drain</strong> again to uncordon.</div>`;
  }

  document.getElementById('summary-content').innerHTML = h;
}
