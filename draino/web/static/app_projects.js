'use strict';

let projectSearchTimer = null;
let projectCloneStatusTimer = null;
const projectCloneState = {
  open: false,
  mode: 'planner',
  workflowTab: 'workflow',
  source: null,
  sourceLoading: false,
  sourceLoadAttempted: false,
  flavors: [],
  flavorsLoaded: false,
  flavorsLoading: false,
  flavorsError: '',
  keypairs: [],
  keypairsLoaded: false,
  keypairsLoading: false,
  keypairsError: '',
  serverGroups: [],
  serverGroupsLoaded: false,
  serverGroupsLoading: false,
  serverGroupsError: '',
  newName: '',
  flavorId: '',
  keyName: '',
  serverGroupId: '',
  cloneDataVolumes: true,
  createFloatingIp: true,
  powerOn: true,
  keepMetadata: true,
  userDataMode: 'preserve',
  userDataText: '',
  selectedSecurityGroupIds: [],
  portOverrides: {},
  submitting: false,
  requestId: '',
  activeCloneLabel: '',
  trackedInstanceId: '',
  status: null,
  statusPollError: '',
  message: '',
  tone: 'info',
};

const PROJECT_CLONE_SOURCE_TIMEOUT_MS = 15000;

function projectCloneStatusState() {
  return String(projectCloneState.status?.state || '').toLowerCase();
}

function isProjectCloneActive() {
  return ['queued', 'running'].includes(projectCloneStatusState());
}

function projectCloneProjectNetworks() {
  const networking = projectInventory('networking') || {};
  return Array.isArray(networking.networks) ? networking.networks : [];
}

function projectCloneFlavorOptions() {
  return Array.isArray(projectCloneState.flavors) ? projectCloneState.flavors : [];
}

function projectCloneKeypairOptions() {
  return Array.isArray(projectCloneState.keypairs) ? projectCloneState.keypairs : [];
}

function projectCloneServerGroupOptions() {
  return Array.isArray(projectCloneState.serverGroups) ? projectCloneState.serverGroups : [];
}

function projectCloneSecurityGroupOptions() {
  const security = projectInventory('security') || {};
  return Array.isArray(security.security_groups) ? security.security_groups : [];
}

function projectViewLabel(name = activeProjectView) {
  return ({
    overview: 'Overview',
    instances: 'Instances',
    networking: 'Networking',
    storage: 'Storage',
    security: 'Security',
    quota: 'Quota + Capacity',
  })[name] || 'Projects';
}

function projectSectionKey(name = activeProjectView) {
  return ({
    overview: 'overview',
    instances: 'instances',
    networking: 'networking',
    storage: 'storage',
    security: 'security',
    quota: 'quota',
  })[name] || 'overview';
}

function projectNetworkingFamilyKey(name = activeProjectNetworkingView) {
  return ({
    networks: 'networks',
    routers: 'routers',
    ports: 'ports',
    floatingips: 'floating_ips',
    loadbalancers: 'load_balancers',
  })[name] || '';
}

function allProjectNetworkingFamilies() {
  return ['networks', 'routers', 'ports', 'floating_ips', 'load_balancers'];
}

function projectInventoryPendingKey(section = projectSectionKey(activeProjectView), family = '') {
  const key = projectSectionKey(section);
  const normalizedFamily = String(family || '').trim();
  return key === 'networking' && normalizedFamily ? `${key}:${normalizedFamily}` : key;
}

function projectRefreshTimestamp(projectId = selectedProjectId, section = activeProjectView, family = '') {
  const key = projectInventoryPendingKey(section, family);
  return projectInventoryState.refreshedAt?.[projectId]?.[key] || 0;
}

function formatProjectRefreshTimestamp(value) {
  const ts = Number(value || 0);
  if (!ts) return 'never';
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (_) {
    return 'just now';
  }
}

function projectRefreshMeta(section = activeProjectView, family = '') {
  return `<span class="hint">Last refreshed ${esc(formatProjectRefreshTimestamp(projectRefreshTimestamp(selectedProjectId, section, family)))}</span>`;
}

function resetProjectQuotaEditor() {
  projectQuotaEditState.section = '';
  projectQuotaEditState.resource = '';
  projectQuotaEditState.value = '';
  projectQuotaEditState.saving = false;
  projectQuotaEditState.error = '';
}

function markProjectQuotaSaveSuccess(section, resource) {
  projectQuotaEditState.successKey = `${section}:${resource}`;
}

function switchProjectSection(name) {
  const valid = ['overview', 'instances', 'networking', 'storage', 'security', 'quota'];
  if (!valid.includes(name)) return;
  activeProjectView = name;
  projectInventoryState.filter = '';
  resetProjectQuotaEditor();
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  if (activeView === 'projects') {
    renderProjectsWorkspace();
    if (selectedProjectId) {
      if (name === 'networking') {
        ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
      } else {
        loadProjectInventory(
          selectedProjectId,
          projectSectionKey(name),
          false,
          '',
        );
      }
    }
  }
}

function switchProjectNetworkingView(name) {
  const valid = ['networks', 'routers', 'ports', 'floatingips', 'loadbalancers'];
  if (!valid.includes(name)) return;
  activeProjectNetworkingView = name;
  projectInventoryState.filter = '';
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
  if (activeView === 'projects' && selectedProjectId) {
    ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
  }
}

function switchProjectStorageView(name) {
  const valid = ['volumes'];
  if (!valid.includes(name)) return;
  activeProjectStorageView = name;
  projectInventoryState.filter = '';
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
}

function projectFilterRows(items, fields, filter) {
  const q = String(filter || '').trim().toLowerCase();
  if (!q) return items || [];
  return (items || []).filter((item) => fields.some((field) => String(item?.[field] || '').toLowerCase().includes(q)));
}

function projectSelectedRecord() {
  return (projectsState.data || []).find((item) => item.project_id === selectedProjectId) || null;
}

function projectInventory(section = activeProjectView) {
  if (!selectedProjectId) return null;
  const cache = projectInventoryState.sections[selectedProjectId] || {};
  return cache[projectSectionKey(section)] || null;
}

function projectInventoryForView(section = activeProjectView) {
  const inv = projectInventory(section);
  if (inv) return inv;
  if (section === 'quota') {
    const overview = projectInventory('overview');
    if (overview?.overview) {
      return {
        summary: overview.summary || {},
        quotas: overview.overview.quotas || {},
        placement: overview.overview.placement || null,
      };
    }
  }
  return null;
}

function projectSectionCache(projectId = selectedProjectId) {
  return projectInventoryState.sections[projectId] || {};
}

function isProjectSectionLoading(projectId, section) {
  const key = projectInventoryPendingKey(section);
  return Boolean(projectInventoryState.pending?.[projectId]?.[key]);
}

function isProjectNetworkingFamilyLoading(projectId, family = projectNetworkingFamilyKey()) {
  return Boolean(projectInventoryState.pending?.[projectId]?.[projectInventoryPendingKey('networking', family)]);
}

function ensureProjectNetworkingFamiliesLoaded(projectId, force = false) {
  if (!projectId) return;
  allProjectNetworkingFamilies().forEach((family) => {
    loadProjectInventory(projectId, 'networking', force, family);
  });
}

function projectHasBackgroundLoading(projectId, activeSection = activeProjectView) {
  const pending = projectInventoryState.pending?.[projectId] || {};
  const activeKey = projectSectionKey(activeSection);
  const activeNetworkingKey = activeKey === 'networking' ? projectInventoryPendingKey('networking', projectNetworkingFamilyKey()) : '';
  return Object.keys(pending).some((key) => key !== activeKey && key !== activeNetworkingKey && pending[key]);
}

function projectOverviewStats(projectId = selectedProjectId) {
  const cache = projectSectionCache(projectId);
  const overview = cache.overview?.overview || {};
  const overviewInstanceSummary = overview.instance_summary || {};
  const instances = cache.instances?.instances || [];
  const networking = cache.networking || {};
  const storage = cache.storage || {};
  const security = cache.security || {};
  const quotas = cache.quota?.quotas || overview.quotas || {};
  const placement = cache.quota?.placement || overview.placement || null;
  const activeInstances = instances.filter((item) => item.status === 'ACTIVE');
  const errorInstances = instances.filter((item) => item.status === 'ERROR');
  const networkingLoaded = ['networks', 'routers', 'ports', 'floating_ips', 'load_balancers'].every((key) => Array.isArray(networking[key]));
  const overviewInstancesReady = overviewInstanceSummary.instance_count != null;
  const instancesLoaded = Boolean(cache.instances) || overviewInstancesReady;
  return {
    instancesLoaded,
    networkingLoaded,
    storageLoaded: Boolean(cache.storage),
    securityLoaded: Boolean(cache.security),
    quotaLoaded: Boolean(cache.quota || cache.overview),
    instanceCount: cache.instances ? instances.length : Number(overviewInstanceSummary.instance_count || 0),
    activeInstanceCount: cache.instances ? activeInstances.length : Number(overviewInstanceSummary.active_instance_count || 0),
    errorInstanceCount: cache.instances ? errorInstances.length : Number(overviewInstanceSummary.error_instance_count || 0),
    cloneCandidateCount: cache.instances ? activeInstances.filter((item) => !String(item.name || '').startsWith('amphora-')).length : Number(overviewInstanceSummary.clone_candidate_count || 0),
    vcpuCount: cache.instances ? instances.reduce((sum, item) => sum + Number(item.vcpus || 0), 0) : Number(overviewInstanceSummary.vcpu_count || 0),
    ramMb: cache.instances ? instances.reduce((sum, item) => sum + Number(item.ram_mb || 0), 0) : Number(overviewInstanceSummary.ram_mb || 0),
    networkCount: (networking.networks || []).length,
    routerCount: (networking.routers || []).length,
    portCount: (networking.ports || []).length,
    floatingIpCount: (networking.floating_ips || []).length,
    loadBalancerCount: (networking.load_balancers || []).length,
    volumeCount: (storage.volumes || []).length,
    securityGroupCount: (security.security_groups || []).length,
    quotas,
    placement,
  };
}

function projectSummary(summary, inv = null) {
  const stats = projectOverviewStats((inv?.summary?.project_id || summary?.project_id || selectedProjectId));
  return {
    ...(projectSelectedRecord() || {}),
    ...(inv?.summary || {}),
    ...(summary || {}),
    ...(stats.instancesLoaded ? {
      instance_count: stats.instanceCount,
      active_instance_count: stats.activeInstanceCount,
      error_instance_count: stats.errorInstanceCount,
      clone_candidate_count: stats.cloneCandidateCount,
      vcpu_count: stats.vcpuCount,
      ram_mb: stats.ramMb,
    } : {}),
    ...(stats.networkingLoaded ? {
      network_count: stats.networkCount,
      router_count: stats.routerCount,
      port_count: stats.portCount,
      floating_ip_count: stats.floatingIpCount,
      load_balancer_count: stats.loadBalancerCount,
    } : {}),
    ...(stats.storageLoaded ? {
      volume_count: stats.volumeCount,
    } : {}),
    ...(stats.securityLoaded ? {
      security_group_count: stats.securityGroupCount,
    } : {}),
    ...(stats.placement ? {
      host_count: stats.placement.host_count,
      top_host: stats.placement.top_host,
      top_host_pct: stats.placement.top_host_pct,
    } : {}),
  };
}

function projectValue(value, loaded, suffix = '') {
  if (!loaded) return '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>';
  return esc(`${value}${suffix}`);
}

function projectSummaryMetricReady(summary, key) {
  return summary?.[key] != null && summary[key] !== '';
}

function projectNetworkingSummary(summary, stats) {
  const networkQuota = stats?.quotas?.network || {};
  const fromSummary = (key) => projectSummaryMetricReady(summary, key) ? Number(summary[key] || 0) : null;
  const fromQuota = (key) => {
    const entry = networkQuota[key];
    if (!entry || entry.used == null) return null;
    return Number(entry.used || 0);
  };
  const metric = (summaryKey, quotaKey, statsLoaded, statsValue) => {
    const value = stats.networkingLoaded ? statsValue : (fromSummary(summaryKey) ?? fromQuota(quotaKey));
    return {
      ready: stats.networkingLoaded || fromSummary(summaryKey) != null || fromQuota(quotaKey) != null,
      value: value ?? 0,
    };
  };
    return {
    networks: metric('network_count', 'network', stats.networkingLoaded, stats.networkCount),
    routers: metric('router_count', 'router', stats.networkingLoaded, stats.routerCount),
    ports: metric('port_count', 'port', stats.networkingLoaded, stats.portCount),
    floatingIps: metric('floating_ip_count', 'floatingip', stats.networkingLoaded, stats.floatingIpCount),
    loadBalancers: metric('load_balancer_count', 'load_balancer', stats.networkingLoaded, stats.loadBalancerCount),
  };
}

function openProjectQuotaEditor(section, resource, currentLimit) {
  if (!authInfo?.is_admin) return;
  projectQuotaEditState.section = section;
  projectQuotaEditState.resource = resource;
  projectQuotaEditState.value = currentLimit == null ? '' : String(currentLimit);
  projectQuotaEditState.saving = false;
  projectQuotaEditState.error = '';
  renderProjectsWorkspace();
}

function updateProjectQuotaEditorValue(value) {
  projectQuotaEditState.value = value;
}

async function saveProjectQuotaEdit() {
  if (!authInfo?.is_admin || !selectedProjectId || projectQuotaEditState.saving) return;
  const section = projectQuotaEditState.section;
  const resource = projectQuotaEditState.resource;
  if (!section || !resource) return;
  projectQuotaEditState.saving = true;
  projectQuotaEditState.error = '';
  renderProjectsWorkspace();
  try {
    const resp = await fetch(`/api/projects/${encodeURIComponent(selectedProjectId)}/quota`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section,
        resource,
        limit: projectQuotaEditState.value,
      }),
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else if (section === 'compute') recordApiSuccess('Nova');
    else if (section === 'network') recordApiSuccess('Neutron');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectInventoryState.sections[selectedProjectId] = {
      ...(projectInventoryState.sections[selectedProjectId] || {}),
      quota: json.inventory || null,
    };
    markProjectQuotaSaveSuccess(section, resource);
    resetProjectQuotaEditor();
    renderProjectsWorkspace();
    loadProjectInventory(selectedProjectId, 'overview', true);
  } catch (err) {
    projectQuotaEditState.error = String(err);
    projectQuotaEditState.saving = false;
    renderProjectsWorkspace();
  }
}

function prefetchProjectDetails(projectId) {
  if (!projectId) return;
  if (projectInventoryState.prefetched?.[projectId]) return;
  projectInventoryState.prefetched = { ...(projectInventoryState.prefetched || {}), [projectId]: true };
  ['instances', 'storage', 'quota'].forEach((section) => {
    if (!projectSectionCache(projectId)[section]) loadProjectInventory(projectId, section);
  });
}

function projectSearchChanged(value) {
  projectsState.filter = value;
  if (projectSearchTimer) clearTimeout(projectSearchTimer);
  projectSearchTimer = setTimeout(() => {
    loadProjects(true);
  }, 300);
}

async function loadProjects(force = false) {
  if (projectsState.loading) return;
  if (projectsState.data && !force) {
    renderProjectsWorkspace();
    return;
  }
  projectsState.loading = true;
  renderProjectsWorkspace();
  try {
    const search = String(projectsState.filter || '').trim();
    const url = search ? `/api/projects?search=${encodeURIComponent(search)}` : '/api/projects';
    const resp = await fetch(url);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    projectsState.data = json.projects || [];
    if ((!selectedProjectId || !projectsState.data.some((item) => item.project_id === selectedProjectId)) && projectsState.data.length) {
      selectedProjectId = projectsState.data[0].project_id;
    }
    if (!projectsState.data.length && !search) selectedProjectId = '';
  } catch (err) {
    projectsState.data = [];
    onLog?.({ node: '-', message: `Projects API error: ${err}`, color: 'error' });
  } finally {
    projectsState.loading = false;
    renderProjectsWorkspace();
    if (selectedProjectId) {
      if (activeProjectView === 'networking') ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
      else loadProjectInventory(
        selectedProjectId,
        projectSectionKey(activeProjectView),
        false,
        '',
      );
      prefetchProjectDetails(selectedProjectId);
    }
  }
}

async function loadProjectInventory(projectId, section = projectSectionKey(activeProjectView), force = false, family = '') {
  if (!projectId) return;
  const key = projectSectionKey(section);
  const normalizedFamily = key === 'networking' ? String(family || '').trim() : '';
  const pendingKey = projectInventoryPendingKey(key, normalizedFamily);
  const cache = projectInventoryState.sections[projectId] || {};
  if (Boolean(projectInventoryState.pending?.[projectId]?.[pendingKey])) return;
  if (key === 'networking' && normalizedFamily) {
    if (cache[key]?.[normalizedFamily] && !force) {
      renderProjectsWorkspace();
      return;
    }
  } else if (cache[key] && !force) {
    renderProjectsWorkspace();
    return;
  }
  projectInventoryState.projectId = projectId;
  projectInventoryState.activeSection = pendingKey;
  projectInventoryState.pending = {
    ...(projectInventoryState.pending || {}),
    [projectId]: {
      ...(projectInventoryState.pending?.[projectId] || {}),
      [pendingKey]: true,
    },
  };
  projectInventoryState.loading = true;
  renderProjectsWorkspace();
  try {
    const params = new URLSearchParams({ section: key });
    if (normalizedFamily) params.set('family', normalizedFamily);
    const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/inventory?${params.toString()}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    if (key === 'networking' && normalizedFamily) {
      const existingSections = projectInventoryState.sections[projectId] || {};
      const existingNetworking = existingSections.networking || {};
      projectInventoryState.sections[projectId] = {
        ...existingSections,
        networking: {
          ...(existingNetworking || {}),
          summary: json.inventory?.summary || existingNetworking.summary || {},
          [normalizedFamily]: json.inventory?.[normalizedFamily] || [],
        },
      };
    } else {
      projectInventoryState.sections[projectId] = {
        ...(projectInventoryState.sections[projectId] || {}),
        [key]: json.inventory || null,
      };
    }
    projectInventoryState.refreshedAt = {
      ...(projectInventoryState.refreshedAt || {}),
      [projectId]: {
        ...(projectInventoryState.refreshedAt?.[projectId] || {}),
        [pendingKey]: Date.now(),
      },
    };
    if (key === 'quota' && projectQuotaEditState.successKey) {
      projectQuotaEditState.successKey = '';
    }
  } catch (err) {
    if (key !== 'networking' || !normalizedFamily) {
      projectInventoryState.sections[projectId] = {
        ...(projectInventoryState.sections[projectId] || {}),
        [key]: null,
      };
    }
    onLog?.({ node: '-', message: `Project inventory API error: ${err}`, color: 'error' });
  } finally {
    if (projectInventoryState.pending?.[projectId]) {
      const nextPending = { ...(projectInventoryState.pending[projectId] || {}) };
      delete nextPending[pendingKey];
      projectInventoryState.pending = {
        ...(projectInventoryState.pending || {}),
        [projectId]: nextPending,
      };
    }
    projectInventoryState.loading = Object.values(projectInventoryState.pending || {}).some((items) => Object.values(items || {}).some(Boolean));
    renderProjectsWorkspace();
  }
}

function refreshActiveProjectView() {
  if (projectsState.loading) return;
  if (!projectsState.data) return loadProjects(true);
  if (selectedProjectId) {
    if (activeProjectView === 'networking') {
      ensureProjectNetworkingFamiliesLoaded(selectedProjectId, true);
      return;
    }
    return loadProjectInventory(
      selectedProjectId,
      projectSectionKey(activeProjectView),
      true,
      '',
    );
  }
  return loadProjects(true);
}

function selectProject(projectId) {
  if (!projectId || selectedProjectId === projectId) {
    renderProjectsWorkspace();
    return;
  }
  selectedProjectId = projectId;
  resetProjectQuotaEditor();
  projectInventoryState.projectId = projectId;
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
  if (activeProjectView === 'networking') ensureProjectNetworkingFamiliesLoaded(projectId, true);
  else loadProjectInventory(
    projectId,
    projectSectionKey(activeProjectView),
    true,
    '',
  );
  prefetchProjectDetails(projectId);
}

function renderProjectsSidebar() {
  const wrap = document.getElementById('projects-sidebar');
  if (!wrap) return;
  const projects = projectsState.data || [];
  wrap.innerHTML = `
    <div class="sidebar-head">Navigator — Projects</div>
    <div class="project-sidebar-filter">
      <input class="dv-filter" type="text" placeholder="Filter projects…" value="${esc(projectsState.filter || '')}" oninput="projectSearchChanged(this.value)">
    </div>
    <div class="project-sidebar-list">
      ${(projectsState.loading && !projects.length) ? '<div class="project-sidebar-empty"><span class="spinner">⟳</span> Loading projects…</div>' : ''}
      ${projects.map((item) => `
        <div class="project-record ${selectedProjectId === item.project_id ? 'selected' : ''}" onclick="selectProject('${escAttr(item.project_id)}')">
          <div class="project-record-name">${esc(item.project_name || item.project_id)}</div>
          <div class="project-record-meta">${esc(item.project_id || '')}</div>
          ${item.description ? `<div class="project-record-meta">${esc(item.description)}</div>` : ''}
        </div>
      `).join('') || (!projectsState.loading ? '<div class="project-sidebar-empty">No projects match the current filter.</div>' : '')}
    </div>
  `;
}

function renderProjectToolbar(title, count, placeholder, extra = '') {
  const section = activeProjectView;
  const family = section === 'networking' ? projectNetworkingFamilyKey() : '';
  return `
    <div class="data-view-toolbar">
      <h2>${esc(title)} <span class="hint">Project Scoped</span></h2>
      ${extra}
      ${projectRefreshMeta(section, family)}
      <button class="btn" onclick="refreshActiveProjectView()">⟳ Refresh</button>
      <input class="dv-filter" type="text" placeholder="${esc(placeholder)}" value="${esc(projectInventoryState.filter || '')}" oninput="projectInventoryState.filter=this.value;renderProjectsWorkspace()">
      <span style="font-size:11px;color:var(--dim)">${esc(String(count || 0))} item(s)</span>
    </div>
  `;
}

function renderProjectSectionToolbar(title, extra = '') {
  const section = activeProjectView;
  const family = section === 'networking' ? projectNetworkingFamilyKey() : '';
  return `
    <div class="data-view-toolbar">
      <h2>${esc(title)} <span class="hint">Project Scoped</span></h2>
      ${extra}
      ${projectRefreshMeta(section, family)}
      <button class="btn" onclick="refreshActiveProjectView()">⟳ Refresh</button>
    </div>
  `;
}

function projectCloneSourceFor(instanceId) {
  if (projectDetailState.kind === 'instances' && projectDetailState.item?.id === instanceId) {
    return projectDetailState.data || projectDetailState.item || null;
  }
  const inv = projectInventoryForView('instances');
  return (inv?.instances || []).find((item) => item.id === instanceId) || null;
}

function projectCloneVolumeRows(instance) {
  const vols = Array.isArray(instance?.volumes) ? instance.volumes : [];
  const rootId = String(instance?.root_volume_id || '');
  return vols.map((vol) => {
    const isRoot = rootId && vol.id === rootId;
    return {
      id: vol.id || '',
      name: vol.name || vol.id || 'Unnamed volume',
      size: vol.size_gb,
      role: isRoot ? 'Root volume' : 'Data volume',
      cloneable: isRoot ? true : !vol.multiattach,
      reason: isRoot ? 'Boot source for the new server.' : (vol.multiattach ? 'Skip by default because the volume is multiattach.' : 'Can be cloned and attached to the new server.'),
    };
  });
}

async function ensureProjectCloneFlavorsLoaded(force = false) {
  if ((projectCloneState.flavorsLoaded && !force) || projectCloneState.flavorsLoading) return;
  projectCloneState.flavorsLoading = true;
  projectCloneState.flavorsError = '';
  if (projectCloneState.open) renderProjectCloneOverlay();
  try {
    const resp = await fetch('/api/flavors');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectCloneState.flavors = Array.isArray(json.flavors) ? json.flavors : [];
    projectCloneState.flavorsLoaded = true;
  } catch (err) {
    projectCloneState.flavors = [];
    projectCloneState.flavorsError = String(err);
  } finally {
    projectCloneState.flavorsLoading = false;
    if (projectCloneState.open) renderProjectCloneOverlay();
  }
}

async function ensureProjectCloneKeypairsLoaded(force = false) {
  if ((projectCloneState.keypairsLoaded && !force) || projectCloneState.keypairsLoading) return;
  projectCloneState.keypairsLoading = true;
  projectCloneState.keypairsError = '';
  if (projectCloneState.open) renderProjectCloneOverlay();
  try {
    const resp = await fetch('/api/keypairs');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectCloneState.keypairs = Array.isArray(json.keypairs) ? json.keypairs : [];
    projectCloneState.keypairsLoaded = true;
  } catch (err) {
    projectCloneState.keypairs = [];
    projectCloneState.keypairsError = String(err);
  } finally {
    projectCloneState.keypairsLoading = false;
    if (projectCloneState.open) renderProjectCloneOverlay();
  }
}

async function ensureProjectCloneServerGroupsLoaded(force = false) {
  if ((projectCloneState.serverGroupsLoaded && !force) || projectCloneState.serverGroupsLoading) return;
  projectCloneState.serverGroupsLoading = true;
  projectCloneState.serverGroupsError = '';
  if (projectCloneState.open) renderProjectCloneOverlay();
  try {
    const resp = await fetch('/api/server-groups');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectCloneState.serverGroups = Array.isArray(json.server_groups) ? json.server_groups : [];
    projectCloneState.serverGroupsLoaded = true;
  } catch (err) {
    projectCloneState.serverGroups = [];
    projectCloneState.serverGroupsError = String(err);
  } finally {
    projectCloneState.serverGroupsLoading = false;
    if (projectCloneState.open) renderProjectCloneOverlay();
  }
}

function projectCloneDefaultFlavorId(instance) {
  return String(instance?.flavor?.id || '').trim();
}

function projectCloneDefaultKeyName(instance) {
  return String(instance?.key_name || '').trim();
}

function projectCloneDefaultServerGroupId(instance) {
  return '';
}

function projectCloneUserDataReadable(instance) {
  return !!instance?.user_data_visible;
}

function projectCloneUserDataPresent(instance) {
  return !!instance?.user_data_present;
}

function projectClonePortOverride(sourcePortId) {
  return projectCloneState.portOverrides?.[sourcePortId] || {};
}

function projectClonePortTargetNetworkId(port) {
  return String(projectClonePortOverride(port.id).networkId || port.network_id || '').trim();
}

function projectClonePortTargetSubnetId(port) {
  return String(projectClonePortOverride(port.id).subnetId || '').trim();
}

function projectClonePortFixedIp(port) {
  return String(projectClonePortOverride(port.id).fixedIp || '').trim();
}

function projectCloneTargetNetworkName(networkId) {
  const match = projectCloneProjectNetworks().find((item) => item.id === networkId);
  return match?.name || networkId || '—';
}

function projectCloneKeypairLabel(keypair) {
  const name = String(keypair?.name || '').trim();
  const fingerprint = String(keypair?.fingerprint || '').trim();
  if (name && fingerprint) return `${name} (${fingerprint})`;
  return name || fingerprint || 'No key pair';
}

function projectCloneServerGroupLabel(group) {
  const name = String(group?.name || '').trim();
  const id = String(group?.id || '').trim();
  const policy = String(group?.policy || (Array.isArray(group?.policies) ? group.policies[0] : '') || '').trim();
  const base = name && id ? `${name} (${id})` : (name || id || 'No server group');
  return policy ? `${base} · ${policy}` : base;
}

function projectCloneIsValidIpv4(value) {
  const parts = String(value || '').trim().split('.');
  if (parts.length !== 4) return false;
  return parts.every((part) => {
    if (!/^\d+$/.test(part)) return false;
    if (part.length > 1 && part.startsWith('0')) return false;
    const num = Number(part);
    return num >= 0 && num <= 255;
  });
}

function projectCloneIsValidIpv6(value) {
  const candidate = String(value || '').trim();
  if (!candidate || !candidate.includes(':')) return false;
  try {
    const normalized = new URL(`http://[${candidate}]/`).hostname;
    return normalized.toLowerCase() === candidate.toLowerCase();
  } catch (_) {
    return false;
  }
}

function projectClonePortIpValidation(port) {
  const value = projectClonePortFixedIp(port);
  if (!value) return '';
  return (projectCloneIsValidIpv4(value) || projectCloneIsValidIpv6(value))
    ? ''
    : 'Enter a valid IPv4 or IPv6 address.';
}

function projectCloneValidationErrors(instance) {
  const errors = [];
  for (const port of (instance?.ports || [])) {
    const ipError = projectClonePortIpValidation(port);
    if (ipError) {
      errors.push(`${port.id || port.network_name || 'port'}: ${ipError}`);
    }
  }
  return errors;
}

function projectClonePreflightReady(instance) {
  return !projectCloneState.sourceLoading && !projectCloneNeedsDetail(instance) && !projectCloneState.flavorsLoading && !projectCloneState.keypairsLoading && !projectCloneState.serverGroupsLoading;
}

function projectCloneLaunchLabel(instance) {
  if (projectCloneState.submitting) return 'Launching Clone…';
  if (isProjectCloneActive()) return 'Clone Busy…';
  if (projectCloneState.sourceLoading) return 'Loading Source…';
  if (projectCloneNeedsDetail(instance)) return projectCloneState.sourceLoadAttempted ? 'Retry Source' : 'Source Incomplete';
  if (projectCloneState.flavorsLoading) return 'Loading Flavors…';
  if (projectCloneState.keypairsLoading) return 'Loading Keypairs…';
  if (projectCloneState.serverGroupsLoading) return 'Loading Groups…';
  if (!projectCloneFlavorReady(instance)) return 'Flavor Missing';
  if (projectCloneValidationErrors(instance).length) return 'Fix IPs';
  return 'Launch Clone';
}

function projectCloneUserDataIntentLabel(instance) {
  if (projectCloneState.userDataMode === 'replace') return 'Replace with new user_data';
  if (projectCloneState.userDataMode === 'none') return 'None';
  return projectCloneUserDataReadable(instance) ? 'Preserve source user_data' : 'Not readable for this user';
}

function projectCloneMetadataIntentLabel() {
  return projectCloneState.keepMetadata ? 'Preserve where possible' : 'Do not preserve';
}

function projectClonePowerIntentLabel() {
  return projectCloneState.powerOn ? 'Boot after build' : 'Create SHUTOFF';
}

function projectCloneFloatingIpIntentLabel(sourceFloatingIps) {
  if (!projectCloneState.createFloatingIp) return 'None';
  return sourceFloatingIps.length ? 'Auto new floating IP' : 'Auto if source network can infer external access';
}

function projectCloneKeypairIntentLabel(instance) {
  const selectedName = String(projectCloneState.keyName || '').trim();
  if (!selectedName) return 'No key pair';
  const selected = projectCloneKeypairOptions().find((item) => item.name === selectedName);
  if (selected) return projectCloneKeypairLabel(selected);
  const sourceName = String(instance?.key_name || '').trim();
  if (sourceName && sourceName === selectedName) return sourceName;
  return selectedName;
}

function projectCloneServerGroupIntentLabel(instance) {
  const selectedId = String(projectCloneState.serverGroupId || '').trim();
  if (!selectedId) return 'No server group';
  const selected = projectCloneServerGroupOptions().find((item) => item.id === selectedId);
  if (selected) return projectCloneServerGroupLabel(selected);
  const sourceId = String(instance?.server_group_id || '').trim();
  if (sourceId && sourceId === selectedId) return sourceId;
  return selectedId;
}

function projectCloneFlavorIntentLabel(instance) {
  const selected = projectCloneFlavorOptions().find((item) => item.id === projectCloneState.flavorId);
  if (selected?.name && selected?.id) return `${selected.name} (${selected.id})`;
  if (selected?.name || selected?.id) return selected?.name || selected?.id;
  if (instance?.flavor?.name && instance?.flavor?.id) return `${instance.flavor.name} (${instance.flavor.id})`;
  return projectCloneState.flavorId || instance?.flavor?.name || instance?.flavor?.id || '—';
}

function projectCloneSecurityGroupLabel(group, includeId = true) {
  const name = String(group?.name || '').trim();
  const id = String(group?.id || '').trim();
  if (name && includeId && id) return `${name} (${id})`;
  return name || id || 'security group';
}

function projectCloneFlavorLabel(flavor) {
  const name = String(flavor?.name || '').trim();
  const id = String(flavor?.id || '').trim();
  if (name && id) return `${name} (${id})`;
  return name || id || '—';
}

function projectClonePortSourceSummary(port) {
  const networkLabel = port?.network_name || port?.network_id || '—';
  const fixedIps = Array.isArray(port?.fixed_ips) && port.fixed_ips.length ? port.fixed_ips.join(', ') : 'Auto';
  const floatingIps = (port?.floating_ips || []).map((ip) => (typeof ip === 'string' ? ip : (ip.address || ip.floating_ip_address || ''))).filter(Boolean);
  const securityGroups = projectClonePortSourceSecurityGroupLabels(port);
  return [
    networkLabel,
    `Fixed IP: ${fixedIps}`,
    `Floating IP: ${floatingIps.join(', ') || 'None'}`,
    `SGs: ${securityGroups.join(', ') || 'None'}`,
  ].join(' | ');
}

function projectClonePortTargetSummary(port) {
  const targetNetwork = projectCloneTargetNetworkName(projectClonePortTargetNetworkId(port));
  const fixedIp = projectClonePortFixedIp(port) || 'Auto';
  const floatingIp = projectCloneState.createFloatingIp ? 'Auto new floating IP' : 'None';
  const securityGroups = projectClonePortSelectedSecurityGroupLabels(port);
  return [
    targetNetwork,
    `Fixed IP: ${fixedIp}`,
    `Floating IP: ${floatingIp}`,
    `SGs: ${securityGroups.join(', ') || 'None'}`,
  ].join(' | ');
}

function projectClonePortSecurityGroupIds(port) {
  const overrideGroups = projectClonePortOverride(port.id).securityGroupIds;
  if (Array.isArray(overrideGroups)) return [...overrideGroups];
  return [...new Set(port.security_groups || [])].filter(Boolean);
}

function projectClonePortSourceSecurityGroupLabels(port) {
  const sourceGroups = [...new Set(port.security_groups || [])].filter(Boolean);
  return sourceGroups.map((groupId) => {
    const option = projectCloneSecurityGroupOptions().find((item) => item.id === groupId);
    return projectCloneSecurityGroupLabel(option || { id: groupId, name: port.security_group_names?.find((name) => name === groupId) || groupId }, Boolean(option?.id));
  });
}

function projectClonePortSelectedSecurityGroups(port) {
  const options = projectCloneSecurityGroupOptions();
  return projectClonePortSecurityGroupIds(port).map((id) => options.find((item) => item.id === id) || { id, name: id });
}

function projectClonePortSelectedSecurityGroupLabels(port) {
  return projectClonePortSelectedSecurityGroups(port).map((group) => projectCloneSecurityGroupLabel(group));
}

function updateProjectClonePortSecurityGroupFromPicker(sourcePortId, value) {
  const nextId = String(value || '').trim();
  if (!nextId) return;
  const current = { ...(projectCloneState.portOverrides?.[sourcePortId] || {}) };
  const next = new Set(Array.isArray(current.securityGroupIds) ? current.securityGroupIds : []);
  next.add(nextId);
  current.securityGroupIds = [...next];
  projectCloneState.portOverrides = {
    ...(projectCloneState.portOverrides || {}),
    [sourcePortId]: current,
  };
  renderProjectCloneOverlay();
}

function removeProjectClonePortSecurityGroup(sourcePortId, groupId) {
  const current = { ...(projectCloneState.portOverrides?.[sourcePortId] || {}) };
  current.securityGroupIds = (Array.isArray(current.securityGroupIds) ? current.securityGroupIds : []).filter((id) => id !== groupId);
  projectCloneState.portOverrides = {
    ...(projectCloneState.portOverrides || {}),
    [sourcePortId]: current,
  };
  renderProjectCloneOverlay();
}

function projectCloneNeedsDetail(instance) {
  if (!instance) return true;
  return !Array.isArray(instance.ports) || !('user_data_visible' in instance);
}

async function ensureProjectCloneSourceDetail(instanceId, force = false) {
  if (!instanceId || projectCloneState.sourceLoading) return;
  if (projectCloneState.sourceLoadAttempted && !force) return;
  const current = projectCloneSourceFor(instanceId);
  if (!projectCloneNeedsDetail(current)) return;
  projectCloneState.sourceLoadAttempted = true;
  projectCloneState.sourceLoading = true;
  projectCloneState.message = 'Loading source instance detail for clone planning.';
  projectCloneState.tone = 'info';
  if (projectCloneState.open) renderProjectCloneOverlay();
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeoutId = controller
    ? setTimeout(() => controller.abort(new Error('clone source timeout')), PROJECT_CLONE_SOURCE_TIMEOUT_MS)
    : 0;
  try {
    const resp = await fetch(`/api/instances/${encodeURIComponent(instanceId)}/clone-source`, controller ? { signal: controller.signal } : undefined);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    const detail = json.instance || null;
    if (detail) {
      if (!Array.isArray(detail.ports)) detail.ports = [];
      if (!Object.prototype.hasOwnProperty.call(detail, 'user_data_visible')) detail.user_data_visible = false;
      if (!Object.prototype.hasOwnProperty.call(detail, 'user_data_present')) detail.user_data_present = false;
      if (!Array.isArray(detail.related_load_balancers)) detail.related_load_balancers = [];
      if (projectDetailState.kind === 'instances' && projectDetailState.item?.id === instanceId) {
        projectDetailState.data = detail;
      }
      projectCloneState.source = detail;
      projectCloneState.flavorId = projectCloneState.flavorId || projectCloneDefaultFlavorId(detail);
      projectCloneState.keyName = projectCloneState.keyName || projectCloneDefaultKeyName(detail);
      projectCloneState.serverGroupId = projectCloneState.serverGroupId || projectCloneDefaultServerGroupId(detail);
      if (!projectCloneUserDataReadable(detail) && projectCloneState.userDataMode === 'preserve') {
        projectCloneState.userDataMode = 'none';
      }
      projectCloneState.portOverrides = {};
      (detail.ports || []).forEach((port) => {
        const defaultSubnetId = Array.isArray(port.subnets) && port.subnets.length === 1 ? (port.subnets[0]?.id || '') : '';
        projectCloneState.portOverrides[port.id] = {
          networkId: port.network_id || '',
          subnetId: defaultSubnetId,
          fixedIp: '',
          securityGroupIds: [...new Set(port.security_groups || [])].filter(Boolean),
        };
      });
      projectCloneState.message = '';
    }
  } catch (err) {
    const aborted = controller && controller.signal?.aborted;
    projectCloneState.message = aborted
      ? `Timed out after ${Math.round(PROJECT_CLONE_SOURCE_TIMEOUT_MS / 1000)}s while loading clone source detail. Retry to try again.`
      : `Failed to load source instance detail: ${err}`;
    projectCloneState.tone = 'error';
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    projectCloneState.sourceLoading = false;
    if (projectCloneState.open) renderProjectCloneOverlay();
  }
}

function openProjectClonePlanner(instanceId) {
  const instance = projectCloneSourceFor(instanceId);
  if (!instance) {
    onLog?.({ node: '-', message: `Clone source ${instanceId} is not loaded yet. Open the instance detail first and try again.`, color: 'warn' });
    return;
  }
  if (isProjectCloneActive()) {
    onLog?.({
      node: '-',
      message: `Clone already in progress for ${projectCloneState.activeCloneLabel || 'another instance'}. Wait for it to finish before starting another clone.`,
      color: 'warn',
    });
    return;
  }
  if (isAmphoraInstance(instance)) {
    onLog?.({ node: '-', message: `Clone is blocked for amphora instance ${instance.name || instance.id || instanceId}.`, color: 'warn' });
    return;
  }
  projectCloneState.open = true;
  projectCloneState.mode = 'planner';
  projectCloneState.workflowTab = 'workflow';
  projectCloneState.source = instance;
  projectCloneState.sourceLoading = projectCloneNeedsDetail(instance);
  projectCloneState.sourceLoadAttempted = false;
  projectCloneState.newName = `vv-${String(instance.name || 'instance').trim() || 'instance'}-clone`;
  projectCloneState.flavorId = projectCloneDefaultFlavorId(instance);
  projectCloneState.keyName = projectCloneDefaultKeyName(instance);
  projectCloneState.serverGroupId = projectCloneDefaultServerGroupId(instance);
  projectCloneState.cloneDataVolumes = true;
  projectCloneState.createFloatingIp = true;
  projectCloneState.powerOn = true;
  projectCloneState.keepMetadata = true;
  projectCloneState.userDataMode = projectCloneUserDataReadable(instance) ? 'preserve' : 'none';
  projectCloneState.userDataText = '';
  projectCloneState.portOverrides = {};
  (instance.ports || []).forEach((port) => {
    const defaultSubnetId = Array.isArray(port.subnets) && port.subnets.length === 1 ? (port.subnets[0]?.id || '') : '';
    projectCloneState.portOverrides[port.id] = {
      networkId: port.network_id || '',
      subnetId: defaultSubnetId,
      fixedIp: '',
      securityGroupIds: [...new Set(port.security_groups || [])].filter(Boolean),
    };
  });
  projectCloneState.message = '';
  projectCloneState.tone = 'info';
  if (projectCloneState.sourceLoading) {
    projectCloneState.message = 'Loading full source instance detail for clone planning.';
  }
  renderProjectCloneOverlay();
  ensureProjectCloneFlavorsLoaded();
  ensureProjectCloneKeypairsLoaded();
  ensureProjectCloneServerGroupsLoaded();
  if (selectedProjectId) ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
  ensureProjectCloneSourceDetail(instanceId);
}

function closeProjectClonePlanner() {
  projectCloneState.open = false;
  projectCloneState.mode = 'planner';
  projectCloneState.workflowTab = 'workflow';
  projectCloneState.source = null;
  if (!isProjectCloneActive()) {
    projectCloneState.message = '';
    projectCloneState.tone = 'info';
  }
  const overlay = document.getElementById('project-clone-overlay');
  if (overlay) overlay.classList.remove('open');
  if (activeView === 'projects') renderProjectsWorkspace();
}

function resetProjectCloneState() {
  stopProjectCloneStatusPolling();
  projectCloneState.open = false;
  projectCloneState.mode = 'planner';
  projectCloneState.workflowTab = 'workflow';
  projectCloneState.source = null;
  projectCloneState.sourceLoading = false;
  projectCloneState.sourceLoadAttempted = false;
  projectCloneState.newName = '';
  projectCloneState.flavorId = '';
  projectCloneState.keyName = '';
  projectCloneState.serverGroupId = '';
  projectCloneState.cloneDataVolumes = true;
  projectCloneState.createFloatingIp = true;
  projectCloneState.powerOn = true;
  projectCloneState.keepMetadata = true;
  projectCloneState.userDataMode = 'preserve';
  projectCloneState.userDataText = '';
  projectCloneState.portOverrides = {};
  projectCloneState.submitting = false;
  projectCloneState.requestId = '';
  projectCloneState.activeCloneLabel = '';
  projectCloneState.trackedInstanceId = '';
  projectCloneState.status = null;
  projectCloneState.statusPollError = '';
  projectCloneState.message = '';
  projectCloneState.tone = 'info';
  const overlay = document.getElementById('project-clone-overlay');
  if (overlay) overlay.classList.remove('open');
  const content = document.getElementById('project-clone-overlay-content');
  if (content) content.innerHTML = '';
}

async function loadProjectCloneStatus(instanceId, force = false) {
  if (!instanceId) return null;
  const previousState = projectCloneStatusState();
  try {
    const qs = force ? '?refresh=1' : '';
    const resp = await fetch(`/api/instances/${encodeURIComponent(instanceId)}/clone-status${qs}`);
    const json = await resp.json();
    projectCloneState.status = json.status || null;
    projectCloneState.statusPollError = '';
    projectCloneState.trackedInstanceId = instanceId;
  } catch (err) {
    projectCloneState.statusPollError = String(err);
    if (!projectCloneState.status) {
      projectCloneState.status = {
        state: 'running',
        source_instance_id: instanceId,
        step_key: 'status_pending',
        step_label: 'Waiting For Status',
        detail: 'Clone is still running, but the latest status refresh failed.',
        error: null,
      };
    }
    projectCloneState.trackedInstanceId = instanceId;
  }
  const nextState = projectCloneStatusState();
  if (nextState !== previousState && ['completed', 'failed'].includes(nextState)) {
    if (nextState === 'completed') {
      const result = projectCloneState.status?.result || {};
      const warningText = (result.warnings || []).filter(Boolean).join(' ');
      projectCloneState.tone = warningText ? 'warn' : 'info';
      projectCloneState.message = `Clone created as ${result.server_name || projectCloneState.status?.new_name || 'new server'} (${result.server_id || 'server id unavailable'}).`
        + `${result.floating_ip_id ? ` Floating IP ${result.floating_ip_id} was allocated.` : projectCloneState.createFloatingIp ? ' No floating IP was allocated.' : ' Floating IP creation was skipped.'}`
        + `${warningText ? ` ${warningText}` : ''}`;
      onLog?.({
        node: '-',
        message: `Clone completed for ${projectCloneState.activeCloneLabel || projectCloneState.status?.source_instance_id || instanceId}: ${result.server_name || projectCloneState.status?.new_name || 'new server'} (${result.server_id || 'unknown server id'}).`,
        color: 'success',
      });
      if (selectedProjectId) {
        loadProjectInventory(selectedProjectId, 'instances', true, '');
        if (activeProjectView === 'networking') ensureProjectNetworkingFamiliesLoaded(selectedProjectId, true);
      }
    } else {
      projectCloneState.tone = 'error';
      projectCloneState.message = `Clone failed during ${projectCloneState.status?.step_label || 'the clone workflow'}: ${projectCloneState.status?.error || projectCloneState.status?.detail || 'Unknown error.'}`;
      onLog?.({
        node: '-',
        message: `Clone failed for ${projectCloneState.activeCloneLabel || projectCloneState.status?.source_instance_id || instanceId} during ${projectCloneState.status?.step_label || 'clone workflow'}: ${projectCloneState.status?.error || projectCloneState.status?.detail || 'Unknown error.'}`,
        color: 'error',
      });
    }
    projectCloneState.activeCloneLabel = '';
  }
  if (activeView === 'projects') renderProjectsWorkspace();
  if (projectCloneState.open) renderProjectCloneOverlay();
  return projectCloneState.status;
}

function stopProjectCloneStatusPolling() {
  if (projectCloneStatusTimer) clearInterval(projectCloneStatusTimer);
  projectCloneStatusTimer = null;
}

function startProjectCloneStatusPolling(instanceId) {
  if (!instanceId) return;
  projectCloneState.trackedInstanceId = instanceId;
  stopProjectCloneStatusPolling();
  loadProjectCloneStatus(instanceId, true);
  projectCloneStatusTimer = setInterval(async () => {
    const status = await loadProjectCloneStatus(instanceId, true);
    if (!status || ['completed', 'failed'].includes(String(status.state || ''))) {
      stopProjectCloneStatusPolling();
    }
  }, 1500);
}

function renderProjectCloneStatusCard() {
  const status = projectCloneState.status;
  if (!status || (!isProjectCloneActive() && !projectCloneState.trackedInstanceId)) return '';
  const state = String(status.state || (isProjectCloneActive() ? 'running' : 'unknown'));
  const panelClass = state === 'failed'
    ? 'clone-activity-panel failed'
    : state === 'completed'
      ? 'clone-activity-panel completed'
      : 'clone-activity-panel running';
  const sourceLabel = projectCloneState.activeCloneLabel || status.new_name || status.source_instance_id || 'clone';
  return `
    <div class="card" style="margin-bottom:10px">
      <div class="card-title" style="display:flex;justify-content:space-between;gap:8px;align-items:center">
        <span>Clone Activity</span>
        <button class="btn" style="font-size:11px" onclick="openTrackedProjectCloneWorkflow()">Open Workflow</button>
      </div>
      <div class="card-body">
        <div class="${panelClass}">
          <div style="font-weight:700;margin-bottom:4px">${esc(sourceLabel)} · ${esc(status.step_label || (state === 'running' ? 'Clone Running' : 'Clone Status'))}</div>
          <div>${esc(status.detail || (isProjectCloneActive() ? 'Clone request is running.' : 'No clone detail available.'))}</div>
          <div style="margin-top:6px;font-size:11px;color:var(--dim)">State: ${esc(state)}</div>
          ${status.error ? `<div style="margin-top:6px;color:var(--red);font-size:11px">${esc(status.error)}</div>` : ''}
          ${projectCloneState.statusPollError ? `<div style="margin-top:6px;color:var(--yellow);font-size:11px">Latest status refresh failed: ${esc(projectCloneState.statusPollError)}</div>` : ''}
        </div>
      </div>
    </div>
  `;
}

function openTrackedProjectCloneWorkflow() {
  const instanceId = projectCloneState.trackedInstanceId || projectCloneState.status?.source_instance_id || '';
  if (!instanceId) return;
  const source = projectCloneSourceFor(instanceId);
  if (source) projectCloneState.source = source;
  projectCloneState.open = true;
  projectCloneState.mode = 'workflow';
  projectCloneState.workflowTab = 'workflow';
  renderProjectCloneOverlay();
}

function switchProjectCloneWorkflowTab(tab) {
  const valid = ['workflow', 'rollback', 'source'];
  if (!valid.includes(String(tab || ''))) return;
  projectCloneState.workflowTab = tab;
  renderProjectCloneOverlay();
}

function projectCloneTimelineEntries() {
  return Array.isArray(projectCloneState.status?.timeline) ? projectCloneState.status.timeline : [];
}

function projectCloneGroupedRows(status = projectCloneState.status) {
  const entries = Array.isArray(status?.timeline) ? status.timeline : [];
  const byKey = new Map();
  entries.forEach((entry) => {
    const key = String(entry.step_key || '').trim();
    if (!key) return;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(entry);
  });
  const flavor = projectCloneState.source?.flavor || {};
  const created = status?.created_resources || {};
  const rows = [];
  const createdPorts = Array.isArray(created.ports) ? created.ports.map((item) => item?.id).filter(Boolean) : [];
  const createdVolumes = Array.isArray(created.volumes) ? created.volumes.map((item) => item?.id).filter(Boolean) : [];
  const createdImages = Array.isArray(created.snapshot_images) ? created.snapshot_images.map((item) => item?.id).filter(Boolean) : [];
  const createdServers = Array.isArray(created.servers) ? created.servers.map((item) => item?.id).filter(Boolean) : [];
  const createdFloatingIps = Array.isArray(created.floating_ips) ? created.floating_ips.map((item) => item?.id).filter(Boolean) : [];
  const sourceVolumeRows = projectCloneState.source ? projectCloneVolumeRows(projectCloneState.source) : [];
  const hasVolumeClonePlan = sourceVolumeRows.some((item) => item.role === 'Root volume' || (projectCloneState.cloneDataVolumes && item.cloneable));

  const addRow = (row) => {
    if (!row) return;
    rows.push(row);
  };

  const failureStep = String(status?.step_key || '').trim();
  const failureError = String(status?.error || '').trim();
  const cloneState = String(status?.state || '').toLowerCase();
  const cloneFailed = cloneState === 'failed';
  const cloneCompleted = cloneState === 'completed';

  addRow({
    key: 'source',
    task: 'Source Instance',
    description: 'Load source server context and resolve the clone flavor.',
    resourceLines: [
      { kind: 'Server', value: status?.source_instance_id || projectCloneState.trackedInstanceId || '—' },
      { kind: 'Flavor', value: projectCloneFlavorLabel(flavor) },
    ],
    state: cloneFailed && (failureStep === 'fetch_source' || failureStep === 'resolve_source') ? 'failed' : (byKey.has('resolve_source') ? 'completed' : byKey.has('fetch_source') ? 'running' : 'pending'),
    statusText: cloneFailed && (failureStep === 'fetch_source' || failureStep === 'resolve_source') ? 'Failed' : byKey.has('resolve_source') ? 'Resolved' : byKey.has('fetch_source') ? 'Resolving' : 'Pending',
    error: cloneFailed && (failureStep === 'fetch_source' || failureStep === 'resolve_source') ? failureError : '',
  });

  addRow({
    key: 'ports',
    task: 'Ports',
    description: 'Resolve and clone Neutron ports as one operation.',
    resourceKind: 'Ports',
    resourceValue: createdPorts.length ? createdPorts.join(', ') : '—',
    state: cloneFailed && (failureStep === 'resolve_ports' || failureStep === 'create_ports') ? 'failed' : (createdPorts.length ? 'completed' : byKey.has('resolve_ports') || byKey.has('create_ports') ? 'running' : 'pending'),
    statusText: cloneFailed && (failureStep === 'resolve_ports' || failureStep === 'create_ports') ? 'Failed' : createdPorts.length ? 'Cloned' : byKey.has('resolve_ports') || byKey.has('create_ports') ? 'Cloning' : 'Pending',
    error: cloneFailed && (failureStep === 'resolve_ports' || failureStep === 'create_ports') ? failureError : '',
  });

  if (byKey.has('clone_volume') || createdVolumes.length || (hasVolumeClonePlan && byKey.has('resolve_volumes'))) {
    addRow({
      key: 'volumes',
      task: 'Volumes',
      description: 'Clone the source volumes selected for the new server.',
      resourceKind: 'Volumes',
      resourceValue: createdVolumes.length ? createdVolumes.join(', ') : 'none',
      state: cloneFailed && (failureStep === 'resolve_volumes' || failureStep === 'clone_volume') ? 'failed' : (createdVolumes.length ? 'completed' : byKey.has('resolve_volumes') || byKey.has('clone_volume') ? 'running' : 'pending'),
      statusText: cloneFailed && (failureStep === 'resolve_volumes' || failureStep === 'clone_volume') ? 'Failed' : createdVolumes.length ? 'Cloned' : byKey.has('resolve_volumes') || byKey.has('clone_volume') ? 'Cloning' : 'Pending',
      error: cloneFailed && (failureStep === 'resolve_volumes' || failureStep === 'clone_volume') ? failureError : '',
    });
  }

  if (byKey.has('create_snapshot') || createdImages.length) {
    const snapshotFailed = cloneFailed && failureStep === 'create_snapshot';
    addRow({
      key: 'snapshot',
      task: 'Snapshot Image',
      description: 'Create a snapshot image when the clone needs an image-backed boot path.',
      resourceKind: 'Snapshot Image',
      resourceValue: createdImages.length ? createdImages.join(', ') : '—',
      state: snapshotFailed ? 'failed' : createdImages.length ? 'completed' : byKey.has('create_snapshot') ? 'running' : 'pending',
      statusText: snapshotFailed ? 'Failed' : createdImages.length ? 'Created' : byKey.has('create_snapshot') ? 'Creating' : 'Pending',
      error: snapshotFailed ? failureError : '',
    });
  }

  if (byKey.has('create_server') || byKey.has('wait_server') || byKey.has('power_off') || byKey.has('completed') || createdServers.length) {
    const serverResource = createdServers.length ? createdServers.join(', ') : (status?.result?.server_id || '');
    const buildFailed = cloneFailed && ['create_server', 'wait_server', 'power_off'].includes(failureStep);
    addRow({
      key: 'build',
      task: 'Server Build',
      description: 'Create the server and wait for the final power state.',
      resourceKind: 'Server',
      resourceValue: serverResource || '—',
      resourceLines: (createdServers.length || status?.result?.server_id) ? [{ kind: 'Server', value: createdServers.join(', ') || status?.result?.server_id || '—' }] : [],
      state: buildFailed ? 'failed' : (cloneCompleted ? 'completed' : byKey.has('create_server') || byKey.has('wait_server') || byKey.has('power_off') ? 'running' : 'pending'),
      statusText: buildFailed ? 'Failed' : (cloneCompleted ? 'Built' : byKey.has('create_server') || byKey.has('wait_server') || byKey.has('power_off') ? 'Building' : 'Pending'),
      error: buildFailed ? failureError : '',
    });
  }

  if (status?.result?.floating_ip_requested || byKey.has('create_floating_ip') || createdFloatingIps.length) {
    const floatingIpFailed = cloneFailed && failureStep === 'create_floating_ip';
    const floatingIpSkipped = byKey.has('create_floating_ip') && !createdFloatingIps.length && !floatingIpFailed && String(status?.state || '').toLowerCase() !== 'running';
    addRow({
      key: 'floating_ip',
      task: 'Floating IP',
      description: 'Allocate and attach a new floating IP if requested.',
      resourceKind: 'Floating IP',
      resourceValue: createdFloatingIps.length ? createdFloatingIps.join(', ') : 'none',
      state: floatingIpFailed ? 'failed' : createdFloatingIps.length ? 'completed' : (floatingIpSkipped ? 'skipped' : byKey.has('create_floating_ip') ? 'running' : 'pending'),
      statusText: floatingIpFailed ? 'Failed' : createdFloatingIps.length ? 'Allocated' : (floatingIpSkipped ? 'Skipped' : byKey.has('create_floating_ip') ? 'Allocating' : 'Pending'),
      error: floatingIpFailed ? failureError : '',
    });
  }

  if (Array.isArray(status?.cleanup_actions) && status.cleanup_actions.length) {
    addRow({
      key: 'cleanup',
      task: 'Rollback',
      description: 'Delete resources that were created before the clone failed.',
      resourceKind: 'Rollback',
      resourceValue: status.cleanup_actions.map((item) => item.resource_id).filter(Boolean).join(', ') || '—',
      state: Array.isArray(status?.residual_resources) && status.residual_resources.length ? 'failed' : 'completed',
      statusText: Array.isArray(status?.residual_resources) && status.residual_resources.length ? 'Partial Cleanup' : 'Cleaned Up',
      error: Array.isArray(status?.residual_resources) && status.residual_resources.length ? `${status.residual_resources.length} leftover resource(s) still need attention.` : '',
    });
  }

  return rows;
}

function projectCloneMessageClass() {
  if (projectCloneState.tone === 'error') return 'clone-workflow-message error';
  if (projectCloneState.tone === 'warn') return 'clone-workflow-message warn';
  return 'clone-workflow-message info';
}

function shouldShowProjectCloneMessage() {
  const state = projectCloneStatusState();
  if (!projectCloneState.message) return false;
  if (state === 'completed') return false;
  return true;
}

function renderProjectCloneWorkflowPanel() {
  const status = projectCloneState.status;
  if (!status) return '';
  const rows = projectCloneGroupedRows(status);
  const cleanupRows = Array.isArray(status.cleanup_actions) ? status.cleanup_actions : [];
  const residualRows = Array.isArray(status.residual_resources) ? status.residual_resources : [];
  const currentTask = rows.find((row) => row.state === 'running') || rows.find((row) => row.state === 'failed') || rows.find((row) => row.state === 'completed') || null;
  const summaryResourceCounts = {
    ports: Array.isArray(status.created_resources?.ports) ? status.created_resources.ports.length : 0,
    volumes: Array.isArray(status.created_resources?.volumes) ? status.created_resources.volumes.length : 0,
    servers: Array.isArray(status.created_resources?.servers) ? status.created_resources.servers.length : 0,
    floatingIps: Array.isArray(status.created_resources?.floating_ips) ? status.created_resources.floating_ips.length : 0,
  };
  return `
    <div class="clone-workflow-layout">
      <div class="card">
        <div class="card-title">Execution Summary</div>
        <div class="card-body">
          <div class="clone-workflow-summary-grid">
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Current Task</div>
              <div class="clone-workflow-summary-value">${esc(currentTask?.task || status.step_label || '—')}</div>
              <div class="clone-workflow-summary-sub">${esc(currentTask?.error || status.detail || 'Waiting for clone activity.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Source Flavor</div>
              <div class="clone-workflow-summary-value">${esc(projectCloneState.source?.flavor?.name || projectCloneState.source?.flavor?.id || '—')}</div>
              <div class="clone-workflow-summary-sub">${esc(projectCloneFlavorReady(projectCloneState.source) ? 'Resolved before launch.' : 'Flavor is unresolved.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Created Resources</div>
              <div class="clone-workflow-summary-value">${esc(String(summaryResourceCounts.ports + summaryResourceCounts.volumes + summaryResourceCounts.servers + summaryResourceCounts.floatingIps))}</div>
              <div class="clone-workflow-summary-sub">${esc(`${summaryResourceCounts.ports} ports, ${summaryResourceCounts.volumes} volumes, ${summaryResourceCounts.servers} server(s), ${summaryResourceCounts.floatingIps} floating IP(s)`)}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Rollback Outcome</div>
              <div class="clone-workflow-summary-value">${esc(residualRows.length ? `${residualRows.length} Leftover` : cleanupRows.length ? 'Cleaned Up' : 'Not Needed')}</div>
              <div class="clone-workflow-summary-sub">${esc(residualRows.length ? 'Operator action still required.' : cleanupRows.length ? 'Automatic rollback completed.' : 'No cleanup has been required.')}</div>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Clone Workflow Table</div>
        <div class="card-body" style="padding:0">
          <table class="data-table clone-workflow-table" style="margin-bottom:0">
            <thead>
              <tr>
                <th style="width:40%">Task</th>
                <th style="width:36%">Resource</th>
                <th style="width:24%">Status</th>
              </tr>
            </thead>
            <tbody>
              ${rows.length ? rows.map((row) => {
                return `<tr class="clone-workflow-row clone-workflow-row-${escAttr(row.state)}">
                  <td>
                    <div class="clone-workflow-task-title">${esc(row.task)}</div>
                    <div class="clone-workflow-task-sub">${esc(row.description)}</div>
                    ${row.error ? `<div class="clone-workflow-task-error">${esc(row.error)}</div>` : ''}
                  </td>
                  <td>
                    ${Array.isArray(row.resourceLines) && row.resourceLines.length
                      ? row.resourceLines.map((line) => `<div class="clone-workflow-resource-line"><div class="clone-workflow-resource-kind">${esc(line.kind)}</div><div class="mono">${esc(line.value)}</div></div>`).join('')
                      : `<div class="clone-workflow-resource-kind">${esc(row.resourceKind)}</div><div class="mono">${esc(row.resourceValue)}</div>`}
                  </td>
                  <td><span class="status-pill ${escAttr(row.state === 'completed' ? 'done' : row.state === 'failed' ? 'failed' : row.state === 'running' ? 'running' : row.state === 'skipped' ? 'waiting' : 'waiting')}">${esc(row.statusText)}</span></td>
                </tr>`;
              }).join('') : '<tr><td colspan="3" style="color:var(--dim)">No clone workflow events have been recorded yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}

function renderProjectCloneRollbackPanel() {
  const status = projectCloneState.status;
  if (!status) return '';
  const cleanupRows = Array.isArray(status.cleanup_actions) ? status.cleanup_actions : [];
  const residualRows = Array.isArray(status.residual_resources) ? status.residual_resources : [];
  return `
    <div class="clone-workflow-layout">
      <div class="card">
        <div class="card-title">Rollback Summary</div>
        <div class="card-body">
          <div class="clone-workflow-summary-grid">
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Rollback Attempts</div>
              <div class="clone-workflow-summary-value">${esc(String(cleanupRows.length))}</div>
              <div class="clone-workflow-summary-sub">Cleanup actions recorded for this clone.</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Leftover Resources</div>
              <div class="clone-workflow-summary-value">${esc(String(residualRows.length))}</div>
              <div class="clone-workflow-summary-sub">${esc(residualRows.length ? 'Operator action still required.' : 'No leftover resources remain.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Clone State</div>
              <div class="clone-workflow-summary-value">${esc(String(status.state || 'unknown'))}</div>
              <div class="clone-workflow-summary-sub">Overall workflow state from the backend tracker.</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Current Step</div>
              <div class="clone-workflow-summary-value">${esc(status.step_label || '—')}</div>
              <div class="clone-workflow-summary-sub">${esc(status.detail || 'No additional detail.')}</div>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Operator Recovery</div>
        <div class="card-body">
          <div class="clone-workflow-notice">
            Use this pane for cleanup and follow-up only. Workflow execution details stay in the Workflow tab.
          </div>
          ${cleanupRows.length ? `
            <div class="clone-workflow-section">
              <div class="clone-workflow-section-title">Rollback Attempts</div>
              <table class="data-table" style="margin-bottom:0">
                <thead><tr><th>Resource</th><th>ID</th><th>Outcome</th></tr></thead>
                <tbody>
                  ${cleanupRows.map((item) => `<tr>
                    <td>${esc(item.resource_type || 'resource')}</td>
                    <td class="mono">${esc(item.resource_id || '—')}</td>
                    <td>${esc(item.state || 'unknown')}${item.error ? `: ${esc(item.error)}` : ''}</td>
                  </tr>`).join('')}
                </tbody>
              </table>
            </div>` : '<div class="hint">No rollback actions have been recorded for this clone.</div>'}
          ${residualRows.length ? `
            <div class="clone-workflow-residual-list">
              ${residualRows.map((item) => `<div class="clone-workflow-residual-item">
                <div>
                  <strong>${esc(`${item.resource_type || 'resource'} ${item.resource_id || ''}`)}</strong>
                  <span>Rollback could not delete this resource automatically.</span>
                </div>
                <button class="btn primary" onclick="cleanupProjectCloneResource('${escAttr(status.source_instance_id || projectCloneState.trackedInstanceId || '')}','${escAttr(item.resource_type || '')}','${escAttr(item.resource_id || '')}')">Delete Resource</button>
              </div>`).join('')}
            </div>` : cleanupRows.length ? '<div class="hint">No leftover resources remain after rollback.</div>' : ''}
        </div>
      </div>
    </div>
  `;
}

function renderProjectCloneSourceContextPanel(instance, context = {}) {
  const ports = Array.isArray(context.ports) ? context.ports : [];
  const volumeRows = Array.isArray(context.volumeRows) ? context.volumeRows : [];
  const relatedLbs = Array.isArray(context.relatedLbs) ? context.relatedLbs : [];
  const securityGroupRows = Array.isArray(context.securityGroupRows) ? context.securityGroupRows : [];
  const sourceFloatingIps = Array.isArray(context.sourceFloatingIps) ? context.sourceFloatingIps : [];
  const flavor = instance?.flavor || {};
  const rootVolume = volumeRows.find((item) => item.role === 'Root volume') || null;
  const cloneableDataVolumes = volumeRows.filter((item) => item.role === 'Data volume' && item.cloneable);
  const skippedDataVolumes = volumeRows.filter((item) => item.role === 'Data volume' && !item.cloneable);
  const selectedFlavor = projectCloneFlavorOptions().find((item) => item.id === projectCloneState.flavorId) || null;
  const userDataReadable = projectCloneUserDataReadable(instance);
  return `
    <div class="clone-workflow-layout">
      <div class="card">
        <div class="card-title">Source Summary</div>
        <div class="card-body">
          <div class="clone-workflow-summary-grid">
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Instance</div>
              <div class="clone-workflow-summary-value">${esc(instance?.name || instance?.id || '—')}</div>
              <div class="clone-workflow-summary-sub">${esc(instance?.status || 'UNKNOWN')} on ${esc(instance?.compute_host || 'unknown host')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Flavor</div>
              <div class="clone-workflow-summary-value">${esc(projectCloneFlavorLabel(selectedFlavor || flavor))}</div>
              <div class="clone-workflow-summary-sub">${esc(projectCloneFlavorReady(instance) ? 'Resolved before clone launch.' : 'Flavor is unresolved.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Ports</div>
              <div class="clone-workflow-summary-value">${esc(String(ports.length))}</div>
              <div class="clone-workflow-summary-sub">${esc(sourceFloatingIps.length ? `${sourceFloatingIps.length} floating IP signal(s) found.` : 'No source floating IPs detected.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">User Data</div>
              <div class="clone-workflow-summary-value">${esc(projectCloneState.userDataMode === 'replace' ? 'Replace' : projectCloneState.userDataMode === 'none' ? 'None' : 'Preserve')}</div>
              <div class="clone-workflow-summary-sub">${esc(projectCloneState.userDataMode === 'preserve' ? (userDataReadable ? 'Source user_data is readable and will be preserved when possible.' : 'Source user_data is not readable to this user.') : projectCloneState.userDataMode === 'replace' ? 'Clone will boot with replacement user_data.' : 'Clone will be created without user_data.')}</div>
            </div>
            <div class="clone-workflow-summary-box">
              <div class="clone-workflow-summary-label">Boot Source</div>
              <div class="clone-workflow-summary-value">${esc(rootVolume?.name || (instance?.is_volume_backed ? 'Root volume present' : 'Image / ephemeral'))}</div>
              <div class="clone-workflow-summary-sub">${esc(`${cloneableDataVolumes.length} cloneable data volume(s), ${skippedDataVolumes.length} skipped`)}</div>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Network Plan</div>
        <div class="card-body">
          <table class="data-table clone-context-network-table" style="margin-bottom:0">
            <thead><tr><th>Port</th><th>Source Network</th><th>Clone Target</th><th>Subnet / Router</th><th>Floating IPs</th><th>Security Groups</th></tr></thead>
            <tbody>
              ${ports.length ? ports.map((port) => {
                const subnetLabels = (port.subnets || []).map((subnet) => {
                  const router = subnet?.router?.name || subnet?.router?.id || port.gateway_target || '';
                  return [subnet?.name || subnet?.cidr || subnet?.id || '', router ? `via ${router}` : ''].filter(Boolean).join(' ');
                }).filter(Boolean);
                const floatingIps = (port.floating_ips || []).map((ip) => (typeof ip === 'string' ? ip : (ip.address || ip.floating_ip_address || ''))).filter(Boolean);
                const portSecurityGroups = [...new Set(port.security_group_names || port.security_groups || [])].filter(Boolean);
                const selectedNetworkId = projectClonePortTargetNetworkId(port);
                const targetNetworkName = projectCloneTargetNetworkName(selectedNetworkId);
                return `<tr>
                  <td class="mono">${esc(port.id || '—')}</td>
                  <td>
                    <div>${esc(port.network_name || '—')}</div>
                    <div class="hint mono">${esc(port.network_id || '—')}</div>
                    <div class="hint mono">${esc((port.fixed_ips || []).join(', ') || 'Auto')}</div>
                  </td>
                  <td>
                    <div>${esc(targetNetworkName)}</div>
                    ${selectedNetworkId && targetNetworkName !== selectedNetworkId ? `<div class="hint mono">${esc(selectedNetworkId)}</div>` : ''}
                    <div class="hint mono">${esc(projectClonePortFixedIp(port) || 'Auto')}</div>
                  </td>
                  <td>${esc(subnetLabels.join(', ') || port.gateway_target || '—')}</td>
                  <td class="mono">${esc(floatingIps.join(', ') || 'None')}</td>
                  <td>${esc(portSecurityGroups.map((group, index) => {
                    const resolved = securityGroupRows.find((item) => item.name === group || item.id === group);
                    return projectCloneSecurityGroupLabel(resolved || { id: group, name: group }, Boolean(resolved?.id));
                  }).join(', ') || '—')}</td>
                </tr>`;
              }).join('') : '<tr><td colspan="6" style="color:var(--dim)">No Neutron port metadata is available for this instance.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Security Group Plan</div>
        <div class="card-body">
          <table class="data-table" style="margin-bottom:0">
              <thead><tr><th>Security Group</th><th>Networks</th><th>Attached Ports</th><th>Clone Guidance</th></tr></thead>
              <tbody>
                ${securityGroupRows.length ? securityGroupRows.map((group) => `<tr>
                <td>${esc(projectCloneSecurityGroupLabel(group))}</td>
                <td>${esc(group.networks.join(', ') || '—')}</td>
                <td class="mono">${esc(group.attachedPorts.join(', ') || '—')}</td>
                <td>${esc(group.portCount > 1 ? 'Shared across multiple source ports; confirm the clone still needs the same east-west reachability.' : 'Present on a single source port; safe default is to preserve until application cutover is reviewed.')}</td>
              </tr>`).join('') : '<tr><td colspan="4" style="color:var(--dim)">No security groups are attached to the source instance ports.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Volume Plan</div>
        <div class="card-body">
          <table class="data-table" style="margin-bottom:0">
            <thead><tr><th>Volume</th><th>Role</th><th>Action</th><th>Note</th></tr></thead>
            <tbody>
              ${volumeRows.length ? volumeRows.map((item) => `<tr>
                <td>${esc(item.name)}</td>
                <td>${esc(item.role)}</td>
                <td>${esc(item.role === 'Root volume' ? 'Clone' : (projectCloneState.cloneDataVolumes && item.cloneable ? 'Clone' : 'Skip'))}</td>
                <td>${esc(item.reason)}</td>
              </tr>`).join('') : '<tr><td colspan="4" style="color:var(--dim)">No attached volume metadata is available for this instance.</td></tr>'}
            </tbody>
          </table>
          ${relatedLbs.length ? `<div class="clone-workflow-notice" style="margin-top:10px">Load balancer membership will not be cloned automatically. ${esc(`${relatedLbs.length} load balancer relationship(s) were detected on the source instance.`)}</div>` : ''}
        </div>
      </div>
    </div>
  `;
}

function projectCloneFlavorReady(instance) {
  const selectedFlavorId = String(projectCloneState.flavorId || '').trim();
  if (!selectedFlavorId) return false;
  if (!projectCloneState.flavorsLoaded || !projectCloneState.flavors.length) return Boolean(selectedFlavorId);
  return projectCloneState.flavors.some((item) => item.id === selectedFlavorId);
}

function updateProjectCloneField(field, value) {
  if (!(field in projectCloneState)) return;
  projectCloneState[field] = value;
  renderProjectCloneOverlay();
}

function updateProjectClonePortOverride(sourcePortId, field, value) {
  const current = { ...(projectCloneState.portOverrides?.[sourcePortId] || {}) };
  current[field] = value;
  if (field === 'networkId' && value !== current.networkId) {
    current.subnetId = '';
    current.fixedIp = '';
  }
  if (field === 'networkId') {
    current.subnetId = '';
    current.fixedIp = '';
  }
  projectCloneState.portOverrides = {
    ...(projectCloneState.portOverrides || {}),
    [sourcePortId]: current,
  };
  renderProjectCloneOverlay();
}

function captureProjectCloneFocusState(container) {
  const active = document.activeElement;
  if (!container || !active || !container.contains(active) || !active.matches('[data-clone-field]')) return null;
  return {
    selector: `[data-clone-field="${active.getAttribute('data-clone-field')}"]`,
    start: typeof active.selectionStart === 'number' ? active.selectionStart : null,
    end: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
  };
}

function restoreProjectCloneFocusState(container, state) {
  if (!container || !state?.selector) return;
  const input = container.querySelector(state.selector);
  if (!input) return;
  input.focus();
  if (typeof state.start === 'number' && typeof state.end === 'number') {
    try { input.setSelectionRange(state.start, state.end); } catch (_) {}
  }
}

async function submitProjectClonePlanner() {
  const instance = projectCloneState.source;
  if (!instance || projectCloneState.submitting || isProjectCloneActive()) return;
  if (!projectClonePreflightReady(instance)) {
    projectCloneState.message = 'Clone launch is blocked until the full source detail and flavor list finish loading.';
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    return;
  }
  const validationErrors = projectCloneValidationErrors(instance);
  if (validationErrors.length) {
    projectCloneState.message = `Fix the requested fixed IP values before launching the clone. ${validationErrors[0]}`;
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    return;
  }
  if (!projectCloneFlavorReady(instance)) {
    projectCloneState.message = 'Clone is blocked because the source flavor cannot be resolved.';
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    return;
  }
  const newName = String(projectCloneState.newName || '').trim();
  if (!newName) {
    projectCloneState.message = 'Provide a name for the new server.';
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    const input = document.getElementById('project-clone-name');
    input?.focus();
    return;
  }
  const dataVolumeCount = projectCloneVolumeRows(instance).filter((item) => item.role === 'Data volume' && item.cloneable).length;
  const cloneDataVolumes = !!projectCloneState.cloneDataVolumes;
  const createFloatingIp = !!projectCloneState.createFloatingIp;
  const powerOn = !!projectCloneState.powerOn;
  const keepMetadata = !!projectCloneState.keepMetadata;
  const flavorId = String(projectCloneState.flavorId || '').trim();
  const keyName = String(projectCloneState.keyName || '');
  const serverGroupId = String(projectCloneState.serverGroupId || '');
  const userDataMode = String(projectCloneState.userDataMode || 'preserve').trim().toLowerCase() || 'preserve';
  const userDataText = String(projectCloneState.userDataText || '');
  if (userDataMode === 'preserve' && !projectCloneUserDataReadable(instance)) {
    projectCloneState.message = 'Source user_data is not readable for this user. Choose Replace or None.';
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    return;
  }
  if (userDataMode === 'replace' && !userDataText.trim()) {
    projectCloneState.message = 'Provide replacement user_data or choose None.';
    projectCloneState.tone = 'error';
    renderProjectCloneOverlay();
    const input = document.getElementById('project-clone-userdata');
    input?.focus();
    return;
  }
  const portOverrides = (instance.ports || []).map((port) => {
    const override = projectClonePortOverride(port.id);
    const sameNetwork = String(override.networkId || port.network_id || '') === String(port.network_id || '');
    const sourceSubnetId = Array.isArray(port.subnets) && port.subnets.length === 1 ? String(port.subnets[0]?.id || '') : '';
    return {
      source_port_id: port.id,
      network_id: String(override.networkId || port.network_id || ''),
      subnet_id: sameNetwork ? String(override.subnetId || sourceSubnetId || '') : '',
      fixed_ip: String(override.fixedIp || ''),
      security_group_ids: [...projectClonePortSecurityGroupIds(port)],
    };
  });
  const requestId = `${instance.id}:${Date.now()}`;
  projectCloneState.submitting = true;
  projectCloneState.requestId = requestId;
  projectCloneState.activeCloneLabel = instance.name || instance.id || 'instance';
  projectCloneState.trackedInstanceId = instance.id;
  projectCloneState.status = {
    state: 'running',
    source_instance_id: instance.id,
    step_key: 'queued',
    step_label: 'Queued',
    detail: 'Clone request accepted and waiting for backend progress.',
    error: null,
  };
  projectCloneState.message = `Submitting clone for ${instance.name || instance.id || 'instance'}${cloneDataVolumes ? ` with ${dataVolumeCount} cloned data volume(s)` : ''}. This can take several minutes. You can close this panel while the clone continues.`;
  projectCloneState.tone = 'info';
  onLog?.({
    node: '-',
    message: `Clone requested for ${instance.name || instance.id}${cloneDataVolumes ? ` with ${dataVolumeCount} cloned data volume(s)` : ''}.`,
    color: 'important',
  });
  startProjectCloneStatusPolling(instance.id);
  projectCloneState.mode = 'workflow';
  renderProjectCloneOverlay();
  try {
    const resp = await fetch(`/api/instances/${encodeURIComponent(instance.id)}/clone`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        new_name: newName,
        flavor_id: flavorId,
        key_name: keyName,
        server_group_id: serverGroupId,
        clone_data_volumes: cloneDataVolumes,
        create_floating_ip: createFloatingIp,
        power_on: powerOn,
        keep_metadata: keepMetadata,
        user_data_mode: userDataMode,
        user_data_text: userDataText,
        port_overrides: portOverrides,
      }),
    });
    const json = await resp.json();
    await loadProjectCloneStatus(instance.id, true);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    const result = json.result || {};
    if (projectCloneState.requestId === requestId && projectCloneState.open && projectCloneState.source?.id === instance.id) {
      projectCloneState.tone = 'info';
      projectCloneState.message = `Clone launched for ${instance.name || instance.id || 'instance'}. Follow the status panel for the current workflow step and any failure detail.`;
    }
    onLog?.({
      node: '-',
      message: `Clone launched for ${instance.name || instance.id}. Waiting for backend progress.`,
      color: 'important',
    });
  } catch (err) {
    if (projectCloneState.requestId === requestId && projectCloneState.open && projectCloneState.source?.id === instance.id) {
      projectCloneState.tone = 'error';
      projectCloneState.message = `Clone failed: ${err}`;
    }
    await loadProjectCloneStatus(instance.id, true);
    onLog?.({ node: '-', message: `Clone failed for ${instance.name || instance.id}: ${err}`, color: 'error' });
  } finally {
    if (projectCloneState.requestId === requestId) {
      projectCloneState.submitting = false;
      projectCloneState.requestId = '';
    }
    if (projectCloneState.status && ['completed', 'failed'].includes(String(projectCloneState.status.state || ''))) {
      stopProjectCloneStatusPolling();
      projectCloneState.activeCloneLabel = '';
    }
    if (!projectCloneState.open) renderProjectsWorkspace();
    renderProjectCloneOverlay();
  }
}

async function cleanupProjectCloneResource(instanceId, resourceType, resourceId) {
  if (!instanceId || !resourceType || !resourceId) return;
  onLog?.({
    node: '-',
    message: `Deleting leftover clone ${resourceType} ${resourceId}.`,
    color: 'warn',
  });
  try {
    const resp = await fetch(`/api/instances/${encodeURIComponent(instanceId)}/clone-cleanup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resource_type: resourceType, resource_id: resourceId }),
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    await loadProjectCloneStatus(instanceId, true);
    onLog?.({
      node: '-',
      message: `Deleted leftover clone ${resourceType} ${resourceId}.`,
      color: 'success',
    });
  } catch (err) {
    onLog?.({
      node: '-',
      message: `Failed to delete leftover clone ${resourceType} ${resourceId}: ${err}`,
      color: 'error',
    });
    await loadProjectCloneStatus(instanceId, true);
  }
}

function renderProjectCloneOverlay() {
  const overlay = document.getElementById('project-clone-overlay');
  const content = document.getElementById('project-clone-overlay-content');
  if (!overlay || !content) return;
  if (!projectCloneState.open || !projectCloneState.source) {
    overlay.classList.remove('open');
    content.innerHTML = '';
    return;
  }
  const instance = projectCloneState.source;
  const ports = Array.isArray(instance.ports) ? instance.ports : [];
  const volumeRows = projectCloneVolumeRows(instance);
  const rootVolume = volumeRows.find((item) => item.role === 'Root volume') || null;
  const cloneableDataVolumes = volumeRows.filter((item) => item.role === 'Data volume' && item.cloneable);
  const skippedDataVolumes = volumeRows.filter((item) => item.role === 'Data volume' && !item.cloneable);
  const relatedLbs = Array.isArray(instance.related_load_balancers) ? instance.related_load_balancers : [];
  const sourceFloatingIps = ports
    .flatMap((port) => port.floating_ips || [])
    .map((ip) => (typeof ip === 'string' ? ip : (ip.address || ip.floating_ip_address || '')))
    .filter(Boolean);
  const securityGroups = [...new Set(ports.flatMap((port) => port.security_group_names || []))].filter(Boolean);
  const securityGroupRows = securityGroups.map((name) => {
    const attachedPorts = ports
      .filter((port) => [...new Set(port.security_group_names || port.security_groups || [])].includes(name))
      .map((port) => port.id || port.network_name || port.network_id || 'port')
      .filter(Boolean);
    const networks = ports
      .filter((port) => [...new Set(port.security_group_names || port.security_groups || [])].includes(name))
      .map((port) => port.network_name || port.network_id || '')
      .filter(Boolean);
    return {
      name,
      portCount: attachedPorts.length,
      attachedPorts,
      networks: [...new Set(networks)],
    };
  });
  const flavor = instance.flavor || {};
  const flavorReady = projectCloneFlavorReady(instance);
  const flavorOptions = projectCloneFlavorOptions();
  const keypairOptions = projectCloneKeypairOptions();
  const keypairSelectOptions = [...keypairOptions];
  if (projectCloneState.keyName && !keypairSelectOptions.some((item) => item.name === projectCloneState.keyName)) {
    keypairSelectOptions.unshift({ name: projectCloneState.keyName, fingerprint: '' });
  }
  const serverGroupOptions = projectCloneServerGroupOptions();
  const serverGroupSelectOptions = [...serverGroupOptions];
  if (projectCloneState.serverGroupId && !serverGroupSelectOptions.some((item) => item.id === projectCloneState.serverGroupId)) {
    serverGroupSelectOptions.unshift({
      id: projectCloneState.serverGroupId,
      name: instance.server_group_name || projectCloneState.serverGroupId,
      policy: '',
    });
  }
  const securityGroupOptions = projectCloneSecurityGroupOptions();
  const userDataReadable = projectCloneUserDataReadable(instance);
  const userDataPresent = projectCloneUserDataPresent(instance);
  const projectNetworks = projectCloneProjectNetworks();
  const cloneValidationErrors = projectCloneValidationErrors(instance);
  const clonePreflightReady = projectClonePreflightReady(instance);
  if (projectCloneState.mode === 'planner' && projectCloneNeedsDetail(instance) && !projectCloneState.sourceLoading && !projectCloneState.sourceLoadAttempted) {
    ensureProjectCloneSourceDetail(instance.id);
  }
  const priorShell = content.querySelector('.project-clone-shell');
  const priorScrollTop = priorShell ? priorShell.scrollTop : 0;
  const focusState = captureProjectCloneFocusState(content);
  if (projectCloneState.mode === 'workflow') {
    const workflowState = String(projectCloneState.status?.state || 'unknown');
    const workflowBadge = workflowState === 'completed'
      ? '<span class="badge green">Completed</span>'
      : workflowState === 'failed'
        ? '<span class="badge red">Failed</span>'
        : '<span class="badge blue">Running</span>';
    const leftoverBadge = Array.isArray(projectCloneState.status?.residual_resources) && projectCloneState.status.residual_resources.length
      ? `<span class="badge yellow">${esc(`${projectCloneState.status.residual_resources.length} Leftover`)}</span>`
      : '';
    content.innerHTML = `
      <div class="project-clone-shell" role="dialog" aria-modal="true" aria-labelledby="project-clone-title">
        <div class="clone-workflow-header">
          <div class="clone-workflow-header-icon">C</div>
          <div class="clone-workflow-header-meta">
            <div class="clone-workflow-header-title" id="project-clone-title">${esc(projectCloneState.status?.new_name || projectCloneState.newName || 'Clone Workflow')}</div>
            <div class="clone-workflow-header-sub">Dedicated clone workflow view for ${esc(instance.name || instance.id || 'selected instance')}.</div>
            <div class="clone-workflow-header-badges">
              ${workflowBadge}
              <span class="badge blue">${esc(`Source: ${instance.name || instance.id || 'instance'}`)}</span>
              ${leftoverBadge}
            </div>
          </div>
        </div>
        <div class="clone-workflow-tabs">
          <button class="clone-workflow-tab ${projectCloneState.workflowTab === 'workflow' ? 'active' : ''}" onclick="switchProjectCloneWorkflowTab('workflow')">Workflow</button>
          <button class="clone-workflow-tab ${projectCloneState.workflowTab === 'rollback' ? 'active' : ''}" onclick="switchProjectCloneWorkflowTab('rollback')">Rollback</button>
          <button class="clone-workflow-tab ${projectCloneState.workflowTab === 'source' ? 'active' : ''}" onclick="switchProjectCloneWorkflowTab('source')">Source Context</button>
        </div>
        <div class="session-body">
          ${projectCloneState.workflowTab === 'rollback'
            ? renderProjectCloneRollbackPanel()
            : projectCloneState.workflowTab === 'source'
              ? renderProjectCloneSourceContextPanel(instance, { ports, volumeRows, relatedLbs, securityGroupRows, sourceFloatingIps })
              : renderProjectCloneWorkflowPanel()}
          ${shouldShowProjectCloneMessage() ? `<div class="${projectCloneMessageClass()}">${esc(projectCloneState.message)}</div>` : ''}
          <div class="session-actions" style="justify-content:flex-end">
            <button class="btn" onclick="closeProjectClonePlanner()">${isProjectCloneActive() || projectCloneState.submitting ? 'Close (clone continues)' : 'Close'}</button>
          </div>
        </div>
      </div>
    `;
    overlay.classList.add('open');
    const nextShell = content.querySelector('.project-clone-shell');
    if (nextShell) nextShell.scrollTop = priorScrollTop;
    restoreProjectCloneFocusState(content, focusState);
    return;
  }
  content.innerHTML = `
    <div class="project-clone-shell" role="dialog" aria-modal="true" aria-labelledby="project-clone-title">
      <div class="session-head">
        <div class="session-head-brand">
          <div class="session-brand-icon">C</div>
          <div>
            <div class="session-brand" id="project-clone-title">Clone VM</div>
            <div class="session-kicker">Project-scoped planner for ${esc(instance.name || instance.id || 'selected instance')}.</div>
          </div>
        </div>
        <div class="session-title">Clone Planner</div>
        <div class="session-sub">Review the source workload, pick safe defaults, and stage the clone request.</div>
      </div>
      <div class="session-body">
        <div class="clone-planner-topgrid">
          <div class="session-panel">
            <div class="session-panel-head">
              <div class="session-panel-title">Source Summary</div>
              <div class="session-panel-sub">${esc(instance.is_volume_backed ? 'Volume-backed instance' : 'Image / ephemeral instance')}</div>
            </div>
            <div class="session-panel-body">
              <div class="summary-grid">
                <div class="card">
                  <div class="card-title">Instance</div>
                  <div class="card-body">
                    <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(instance.name || instance.id || '—')}</span></div>
                    <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(instance.status || 'UNKNOWN')}</span></div>
                    <div class="mrow"><span class="ml">Host</span><span class="mv">${esc(instance.compute_host || '—')}</span></div>
                    <div class="mrow"><span class="ml">Flavor</span><span class="mv">${esc(projectCloneFlavorLabel(flavor))}</span></div>
                    <div class="mrow"><span class="ml">Key pair</span><span class="mv">${esc(instance.key_name || 'None')}</span></div>
                    <div class="mrow"><span class="ml">Server group</span><span class="mv">${esc(instance.server_group_name || instance.server_group_id || 'None')}</span></div>
                  </div>
                </div>
                <div class="card">
                  <div class="card-title">Network</div>
                  <div class="card-body">
                    <div class="mrow"><span class="ml">Ports</span><span class="mv">${esc(String(ports.length || 0))}</span></div>
                    <div class="mrow"><span class="ml">Security groups</span><span class="mv">${esc(securityGroupRows.map((group) => group.name || group.id || '—').join(', ') || '—')}</span></div>
                    <div class="mrow"><span class="ml">Load balancer links</span><span class="mv">${esc(String(relatedLbs.length || 0))}</span></div>
                    <div class="mrow"><span class="ml">Boot source</span><span class="mv">${esc(rootVolume?.name || (instance.is_volume_backed ? 'Root volume present' : 'Image / ephemeral disk'))}</span></div>
                  </div>
                </div>
              </div>
              <div class="clone-preflight-grid" style="margin-top:4px">
                <div class="finding compact ${instance.is_volume_backed ? 'good' : 'warn'}">
                  <div class="finding-mark">${instance.is_volume_backed ? '✓' : '!'}</div>
                  <div><strong>${esc(instance.is_volume_backed ? 'Boot volume clone path available.' : 'Boot volume is not visible.' )}</strong>${esc(instance.is_volume_backed ? ' The safest default is to clone the boot volume and create fresh Neutron ports.' : ' This planner falls back to a snapshot-style path until a backend clone API exists.')}</div>
                </div>
                <div class="finding compact ${flavorReady ? 'good' : 'warn'}">
                  <div class="finding-mark">${flavorReady ? '✓' : '!'}</div>
                  <div><strong>${esc(flavorReady ? 'Flavor is resolvable.' : 'Flavor is missing or unresolved.')}</strong>${esc(flavorReady ? ` Clone will request flavor ${(flavorOptions.find((item) => item.id === projectCloneState.flavorId)?.name) || flavor.name || projectCloneState.flavorId || flavor.id || 'source flavor'}.` : ' Clone launch is blocked until the selected flavor can be resolved from Nova.')}</div>
                </div>
                <div class="finding compact ${userDataReadable ? 'good' : 'warn'}">
                  <div class="finding-mark">${userDataReadable ? '✓' : '!'}</div>
                  <div><strong>${esc(userDataReadable ? 'Source user_data is readable.' : 'Source user_data cannot be preserved for this user.')}</strong>${esc(userDataReadable ? ` ${userDataPresent ? 'Admin-visible user_data can be preserved or replaced at clone time.' : 'The field is readable but empty, so preserve will effectively send no user_data.'}` : ' Regular users can still replace it with new user_data or send none.')}</div>
                </div>
                ${!clonePreflightReady ? `<div class="finding compact warn"><div class="finding-mark">!</div><div><strong>Clone preflight is still loading.</strong>${esc(' Launch stays disabled until the planner has loaded full source detail and flavor choices.')}</div></div>` : ''}
                ${!projectCloneState.sourceLoading && projectCloneNeedsDetail(instance) ? `<div class="finding compact bad"><div class="finding-mark">!</div><div><strong>Source detail is incomplete.</strong>${esc(' Retry loading instance detail so the planner can resolve Neutron ports and user-data visibility.')} <button class="btn" style="margin-left:6px;padding:1px 7px;font-size:11px" onclick="ensureProjectCloneSourceDetail('${escAttr(instance.id || '')}', true)">Retry</button></div></div>` : ''}
                ${relatedLbs.length ? `<div class="finding compact warn"><div class="finding-mark">!</div><div><strong>Load balancer membership will not be cloned automatically.</strong>${esc(` ${relatedLbs.length} load balancer relationship(s) were detected on the source instance.`)}</div></div>` : ''}
              </div>
              <div class="project-clone-field" style="margin-top:10px">
                <span>Network and Security Overrides</span>
                <div class="clone-port-edit-list">
                  ${ports.length ? ports.map((port, index) => {
                    const subnetLabels = (port.subnets || []).map((subnet) => {
                      const router = subnet?.router?.name || subnet?.router?.id || port.gateway_target || '';
                      return [subnet?.name || subnet?.cidr || subnet?.id || '', router ? `via ${router}` : ''].filter(Boolean).join(' ');
                    }).filter(Boolean);
                    const selectedNetworkId = projectClonePortTargetNetworkId(port);
                    const selectedNetworkName = projectCloneTargetNetworkName(selectedNetworkId);
                    const fixedIpValue = projectClonePortFixedIp(port);
                    const fixedIpError = projectClonePortIpValidation(port);
                    const networkChoices = projectNetworks.length
                      ? projectNetworks
                      : [{ id: port.network_id || '', name: port.network_name || port.network_id || 'network' }];
                    const selectedPortSecurityGroups = projectClonePortSelectedSecurityGroups(port);
                    return `<div class="clone-port-edit-card">
                      <div class="clone-port-edit-head">
                        <div class="clone-port-edit-title">${esc(`Port ${index + 1}`)}</div>
                        <div class="hint mono">${esc(port.network_name || port.network_id || '—')}</div>
                      </div>
                      <div class="hint" style="margin-bottom:6px">${esc(`Source fixed IPs: ${(port.fixed_ips || []).join(', ') || 'Auto'}${subnetLabels.length ? ` · ${subnetLabels.join(', ')}` : ''}`)}</div>
                      <select class="clone-inline-select" data-clone-field="port-network-${escAttr(port.id || '')}" onchange="updateProjectClonePortOverride('${escAttr(port.id || '')}','networkId', this.value)">
                        ${networkChoices.map((network) => `<option value="${escAttr(network.id || '')}" ${network.id === selectedNetworkId ? 'selected' : ''}>${esc(network.name && network.id ? `${network.name} (${network.id})` : (network.name || network.id || 'network'))}</option>`).join('')}
                      </select>
                      <div class="hint" style="margin-top:4px">${esc(selectedNetworkName)}</div>
                      <input class="clone-inline-input mono ${fixedIpError ? 'invalid' : ''}" data-clone-field="port-fixed-${escAttr(port.id || '')}" type="text" placeholder="auto" value="${escAttr(fixedIpValue)}" oninput="updateProjectClonePortOverride('${escAttr(port.id || '')}','fixedIp', this.value)">
                      <div class="hint">${esc(subnetLabels.join(', ') || port.gateway_target || 'Auto subnet selection')}</div>
                      ${fixedIpError ? `<div class="clone-inline-error">${esc(fixedIpError)}</div>` : ''}
                      <div class="project-clone-field" style="margin-top:8px">
                        <span>Security groups for this port</span>
                        <select class="dv-filter" onchange="updateProjectClonePortSecurityGroupFromPicker('${escAttr(port.id || '')}', this.value); this.value='';">
                          <option value="">Add security group…</option>
                          ${securityGroupOptions
                            .filter((item) => !projectClonePortSecurityGroupIds(port).includes(item.id))
                            .map((item) => `<option value="${escAttr(item.id || '')}">${esc(projectCloneSecurityGroupLabel(item))}</option>`)
                            .join('')}
                        </select>
                      </div>
                      <div class="clone-sg-chiplist">
                        ${selectedPortSecurityGroups.length
                          ? selectedPortSecurityGroups.map((group) => `<button class="clone-sg-chip" type="button" onclick="removeProjectClonePortSecurityGroup('${escAttr(port.id || '')}','${escAttr(group.id || '')}')">${esc(projectCloneSecurityGroupLabel(group))} <span>×</span></button>`).join('')
                          : '<span class="hint">No security groups selected for this port.</span>'}
                      </div>
                    </div>`;
                  }).join('') : '<div class="hint">No Neutron ports are loaded yet for this instance.</div>'}
                </div>
              </div>
            </div>
          </div>

          <div class="session-panel">
            <div class="session-panel-head">
              <div class="session-panel-title">Clone Settings</div>
            </div>
            <div class="session-panel-body">
              <label class="project-clone-field">
                <span>New server name</span>
                <input id="project-clone-name" data-clone-field="newName" class="dv-filter" type="text" value="${escAttr(projectCloneState.newName)}" oninput="updateProjectCloneField('newName', this.value)">
              </label>
              <label class="project-clone-field">
                <span>Flavor</span>
                <select data-clone-field="flavorId" class="dv-filter" onchange="updateProjectCloneField('flavorId', this.value)">
                  ${(flavorOptions.length ? flavorOptions : [{ id: projectCloneState.flavorId || flavor.id || '', name: flavor.name || projectCloneState.flavorId || 'source flavor' }]).map((item) => `<option value="${escAttr(item.id || '')}" ${item.id === projectCloneState.flavorId ? 'selected' : ''}>${esc(projectCloneFlavorLabel(item))}${item.vcpus ? ` · ${item.vcpus} vCPU` : ''}${item.ram_mb ? ` · ${item.ram_mb} MB` : ''}</option>`).join('')}
                </select>
              </label>
              ${projectCloneState.flavorsLoading ? '<div class="hint" style="margin-top:6px">Loading flavor choices…</div>' : ''}
              ${projectCloneState.flavorsError ? `<div class="finding compact warn" style="margin-top:4px"><div class="finding-mark">!</div><div><strong>Flavor list could not be loaded.</strong>${esc(` ${projectCloneState.flavorsError}`)}</div></div>` : ''}
              <label class="project-clone-field">
                <span>Key pair</span>
                <select data-clone-field="keyName" class="dv-filter" onchange="updateProjectCloneField('keyName', this.value)">
                  <option value="" ${!projectCloneState.keyName ? 'selected' : ''}>No key pair</option>
                  ${keypairSelectOptions.map((item) => `<option value="${escAttr(item.name || '')}" ${item.name === projectCloneState.keyName ? 'selected' : ''}>${esc(projectCloneKeypairLabel(item))}</option>`).join('')}
                </select>
              </label>
              ${projectCloneState.keypairsLoading ? '<div class="hint" style="margin-top:6px">Loading key pairs…</div>' : ''}
              ${projectCloneState.keypairsError ? `<div class="finding compact warn" style="margin-top:4px"><div class="finding-mark">!</div><div><strong>Key pair list could not be loaded.</strong>${esc(` ${projectCloneState.keypairsError}`)}</div></div>` : ''}
              <label class="project-clone-field">
                <span>Nova affinity group</span>
                <select data-clone-field="serverGroupId" class="dv-filter" onchange="updateProjectCloneField('serverGroupId', this.value)">
                  <option value="" ${!projectCloneState.serverGroupId ? 'selected' : ''}>No server group</option>
                  ${serverGroupSelectOptions.map((item) => `<option value="${escAttr(item.id || '')}" ${item.id === projectCloneState.serverGroupId ? 'selected' : ''}>${esc(projectCloneServerGroupLabel(item))}</option>`).join('')}
                </select>
              </label>
              ${projectCloneState.serverGroupsLoading ? '<div class="hint" style="margin-top:6px">Loading server groups…</div>' : ''}
              ${projectCloneState.serverGroupsError ? `<div class="finding compact warn" style="margin-top:4px"><div class="finding-mark">!</div><div><strong>Server groups could not be loaded.</strong>${esc(` ${projectCloneState.serverGroupsError}`)}</div></div>` : ''}
              <label class="project-clone-field">
                <span>User data</span>
                <select data-clone-field="userDataMode" class="dv-filter" onchange="updateProjectCloneField('userDataMode', this.value)">
                  <option value="preserve" ${projectCloneState.userDataMode === 'preserve' ? 'selected' : ''} ${userDataReadable ? '' : 'disabled'}>Preserve source user_data</option>
                  <option value="replace" ${projectCloneState.userDataMode === 'replace' ? 'selected' : ''}>Provide new user_data</option>
                  <option value="none" ${projectCloneState.userDataMode === 'none' ? 'selected' : ''}>No user_data</option>
                </select>
              </label>
              <div class="hint" style="margin-top:6px">${esc(userDataReadable ? (userDataPresent ? 'Admin-visible source user_data can be preserved directly.' : 'Source user_data is readable but empty.') : 'Source user_data is not visible to this user. Preserve is disabled.')}</div>
              ${projectCloneState.userDataMode === 'replace' ? `<label class="project-clone-field">
                <span>Replacement user_data</span>
                <textarea id="project-clone-userdata" data-clone-field="userDataText" class="clone-userdata-text" oninput="updateProjectCloneField('userDataText', this.value)">${esc(projectCloneState.userDataText)}</textarea>
              </label>` : ''}
              <div class="hint" style="margin-top:6px">Clone-created resources are prefixed with vv- or vibeview- for attribution and cleanup.</div>
              <label class="project-clone-check"><input data-clone-field="cloneDataVolumes" type="checkbox" ${projectCloneState.cloneDataVolumes ? 'checked' : ''} onchange="updateProjectCloneField('cloneDataVolumes', this.checked)"> Clone attached data volumes when safe</label>
              <label class="project-clone-check"><input data-clone-field="createFloatingIp" type="checkbox" ${projectCloneState.createFloatingIp ? 'checked' : ''} onchange="updateProjectCloneField('createFloatingIp', this.checked)"> Create a new floating IP for the clone by default</label>
              <label class="project-clone-check"><input data-clone-field="powerOn" type="checkbox" ${projectCloneState.powerOn ? 'checked' : ''} onchange="updateProjectCloneField('powerOn', this.checked)"> Boot the new server after create</label>
              <label class="project-clone-check"><input data-clone-field="keepMetadata" type="checkbox" ${projectCloneState.keepMetadata ? 'checked' : ''} onchange="updateProjectCloneField('keepMetadata', this.checked)"> Preserve instance metadata where possible</label>
            </div>
          </div>
        </div>

        <div class="session-panel">
          <div class="session-panel-head">
            <div class="session-panel-title">Deployment Comparison</div>
            <div class="session-panel-sub">Compare the source instance to the clone settings that will be requested.</div>
          </div>
          <div class="session-panel-body">
            <table class="data-table clone-compare-table" style="margin-bottom:0">
              <thead><tr><th>Aspect</th><th>Source</th><th>Clone</th></tr></thead>
              <tbody>
                <tr>
                  <td>Name</td>
                  <td>${esc(instance.name || instance.id || '—')}</td>
                  <td>${esc(projectCloneState.newName || '—')}</td>
                </tr>
                <tr>
                  <td>Flavor</td>
                  <td>${esc(projectCloneFlavorLabel(flavor))}</td>
                  <td>${esc(projectCloneFlavorIntentLabel(instance))}</td>
                </tr>
                <tr>
                  <td>Key Pair</td>
                  <td>${esc(instance.key_name || 'None')}</td>
                  <td>${esc(projectCloneKeypairIntentLabel(instance))}</td>
                </tr>
                <tr>
                  <td>Server Group</td>
                  <td>${esc(instance.server_group_name || instance.server_group_id || 'None')}</td>
                  <td>${esc(projectCloneServerGroupIntentLabel(instance))}</td>
                </tr>
                <tr>
                  <td>Boot Source</td>
                  <td>${esc(rootVolume?.name || (instance.is_volume_backed ? 'Root volume' : 'Image / ephemeral'))}</td>
                  <td>${esc(instance.is_volume_backed ? 'Clone root volume' : 'Snapshot image then build')}</td>
                </tr>
                <tr>
                  <td>Data Volumes</td>
                  <td>${esc(String(volumeRows.filter((item) => item.role === 'Data volume').length))}</td>
                  <td>${esc(projectCloneState.cloneDataVolumes ? `${cloneableDataVolumes.length} cloned` : 'Skipped by setting')}</td>
                </tr>
                <tr>
                  <td>User Data</td>
                  <td>${esc(userDataReadable ? (userDataPresent ? 'Readable and present' : 'Readable but empty') : 'Not readable')}</td>
                  <td>${esc(projectCloneUserDataIntentLabel(instance))}</td>
                </tr>
                <tr>
                  <td>Metadata</td>
                  <td>${esc(Object.keys(instance.metadata || {}).length ? 'Present' : 'None visible')}</td>
                  <td>${esc(projectCloneMetadataIntentLabel())}</td>
                </tr>
                <tr>
                  <td>Power State</td>
                  <td>${esc(instance.status || 'UNKNOWN')}</td>
                  <td>${esc(projectClonePowerIntentLabel())}</td>
                </tr>
                <tr>
                  <td>Floating IPs</td>
                  <td class="mono">${esc(sourceFloatingIps.join(', ') || 'None')}</td>
                  <td>${esc(projectCloneFloatingIpIntentLabel(sourceFloatingIps))}</td>
                </tr>
                <tr>
                  <td>Security Groups</td>
                  <td>${esc(securityGroupRows.map((group) => projectCloneSecurityGroupLabel(group)).join(', ') || '—')}</td>
                  <td>${esc(ports.length ? 'Per-port overrides below' : 'None')}</td>
                </tr>
                ${ports.map((port, index) => `<tr>
                  <td>${esc(`Port ${index + 1}`)}</td>
                  <td>${esc(projectClonePortSourceSummary(port))}</td>
                  <td>${esc(projectClonePortTargetSummary(port))}</td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>

        ${shouldShowProjectCloneMessage() ? `<div class="${projectCloneMessageClass()}">${esc(projectCloneState.message)}</div>` : ''}

        <div class="session-actions" style="justify-content:flex-end">
          <button class="btn" onclick="closeProjectClonePlanner()">${isProjectCloneActive() || projectCloneState.submitting ? 'Close (clone continues)' : 'Close'}</button>
          <button class="btn primary" onclick="submitProjectClonePlanner()" ${(!clonePreflightReady || !flavorReady || cloneValidationErrors.length || projectCloneState.submitting || isProjectCloneActive()) ? 'disabled' : ''}>${projectCloneLaunchLabel(instance)}</button>
        </div>
      </div>
    </div>
  `;
  overlay.classList.add('open');
  const nextShell = content.querySelector('.project-clone-shell');
  if (nextShell) nextShell.scrollTop = priorScrollTop;
  restoreProjectCloneFocusState(content, focusState);
}

function cloneProjectInstance(instanceId) {
  openProjectClonePlanner(instanceId);
}

function findProjectItem(kind, id) {
  const inv = projectInventoryForView(activeProjectView);
  if (!inv) return null;
  if (kind === 'instances') return (inv.instances || []).find((item) => item.id === id) || null;
  if (kind === 'networks') return (inv.networks || []).find((item) => item.id === id) || null;
  if (kind === 'routers') return (inv.routers || []).find((item) => item.id === id) || null;
  if (kind === 'ports') return (inv.ports || []).find((item) => item.id === id) || null;
  if (kind === 'volumes') return (inv.volumes || []).find((item) => item.id === id) || null;
  if (kind === 'securitygroups') return (inv.security_groups || []).find((item) => item.id === id) || null;
  if (kind === 'floatingips') return (inv.floating_ips || []).find((item) => item.id === id) || null;
  if (kind === 'loadbalancers') return (inv.load_balancers || []).find((item) => item.id === id) || null;
  return null;
}

async function selectProjectDetail(kind, item) {
  projectDetailState.kind = kind;
  projectDetailState.item = item || null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();

  if (!item) return;
  const id = item.id || item.project_id || '';
  const detailUrl = ({
    instances: id ? `/api/instances/${encodeURIComponent(id)}` : '',
    volumes: id ? `/api/volumes/${encodeURIComponent(id)}` : '',
    loadbalancers: id ? `/api/load-balancers/${encodeURIComponent(id)}` : '',
    securitygroups: id ? `/api/security-groups/${encodeURIComponent(id)}` : '',
    networks: id ? `/api/networks/${encodeURIComponent(id)}` : '',
    routers: id ? `/api/routers/${encodeURIComponent(id)}` : '',
    ports: id ? `/api/ports/${encodeURIComponent(id)}` : '',
  })[kind] || '';
  if (!detailUrl) return;

  projectDetailState.loading = true;
  renderProjectsWorkspace();
  try {
    const resp = await fetch(detailUrl);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectDetailState.data = json.instance || json.volume || json.load_balancer || json.security_group || json.network || json.router || json.port || null;
    if (kind === 'instances' && projectDetailState.data?.compute_host) {
      const nodeName = findNodeNameForHypervisor(projectDetailState.data.compute_host);
      if (nodeName && projectDetailState.data.id) loadNodeInstancePortStats(nodeName, projectDetailState.data.id, true);
    }
  } catch (err) {
    projectDetailState.error = String(err);
  } finally {
    projectDetailState.loading = false;
    renderProjectsWorkspace();
  }
}

function selectProjectDetailById(kind, id) {
  const item = findProjectItem(kind, id);
  if (!item) return;
  selectProjectDetail(kind, item);
}

function toggleProjectPortDetail(instanceId, portId) {
  if (projectDetailState.kind !== 'instances' || projectDetailState.item?.id !== instanceId) return;
  const current = projectDetailState.item?.expandedPortId || '';
  projectDetailState.item = { ...projectDetailState.item, expandedPortId: current === portId ? '' : portId };
  renderProjectsWorkspace();
}

function renderOverviewCard(title, rows, extraClass = '') {
  return `
    <div class="card ${extraClass}">
      <div class="card-title">${esc(title)}</div>
      <div class="card-body">
        ${rows.map((row) => `<div class="mrow"><span class="ml">${row.label}</span><span class="mv">${row.value}</span></div>`).join('')}
      </div>
    </div>
  `;
}

function isAmphoraInstance(item) {
  return String(item?.name || '').startsWith('amphora-');
}

function renderProjectInstanceRows(items, cloneable, emptyMessage) {
  const cloneBusy = isProjectCloneActive() || projectCloneState.submitting;
  return items.map((item) => `
    <tr class="${projectDetailState.kind === 'instances' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('instances','${escAttr(item.id)}')">
      <td><div style="font-weight:600">${esc(item.name || item.id)}</div><div class="uuid-short" title="${esc(item.id)}">${esc((item.id || '').slice(0, 12))}</div></td>
      <td>${esc(item.status || 'UNKNOWN')}</td>
      <td>${esc(item.flavor?.name || item.flavor?.id || '—')}</td>
      <td>${esc(item.compute_host || '—')}</td>
      <td>${esc((item.networks || []).map((net) => net.name).join(', ') || '—')}</td>
      <td>${item.is_volume_backed ? 'Volume' : 'Image / Ephemeral'}</td>
      <td>${cloneable ? `<button class="btn" style="font-size:11px" onclick="event.stopPropagation();cloneProjectInstance('${escAttr(item.id)}')" ${cloneBusy ? `disabled title="Clone already running for ${escAttr(projectCloneState.activeCloneLabel || 'another instance')}"` : ''}>${cloneBusy ? 'Clone Busy…' : 'Clone'}</button>` : '<span class="hint">Not cloneable</span>'}</td>
    </tr>
  `).join('') || `<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:20px">${esc(emptyMessage)}</td></tr>`;
}

function renderProjectOverview(inv) {
  const summary = projectSummary(inv.summary, inv);
  const stats = projectOverviewStats(summary.project_id || selectedProjectId);
  const placement = stats.placement || {};
  const quotas = stats.quotas || {};
  const computeQuota = quotas.compute || {};
  const networkQuota = quotas.network || {};
  const storageQuota = quotas.block_storage || {};
  const hostPct = Number(placement.top_host_pct || summary.top_host_pct || 0);
  const networkingSummary = projectNetworkingSummary(summary, stats);
  return `
    ${renderProjectSectionToolbar('Overview')}
    <div class="summary-grid" style="margin-bottom:10px">
      ${renderOverviewCard('Workloads', [
        { label: 'Instances', value: projectValue(summary.instance_count || 0, stats.instancesLoaded) },
        { label: 'Clone Candidates', value: projectValue(summary.clone_candidate_count || 0, stats.instancesLoaded) },
        { label: 'Active / Error', value: stats.instancesLoaded ? esc(`${summary.active_instance_count || 0} / ${summary.error_instance_count || 0}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
      ${renderOverviewCard('Networking', [
        { label: 'Networks', value: projectValue(networkingSummary.networks.value, networkingSummary.networks.ready) },
        { label: 'Routers', value: projectValue(networkingSummary.routers.value, networkingSummary.routers.ready) },
        { label: 'Ports', value: projectValue(networkingSummary.ports.value, networkingSummary.ports.ready) },
        { label: 'Floating IPs', value: projectValue(networkingSummary.floatingIps.value, networkingSummary.floatingIps.ready) },
        { label: 'Load Balancers', value: projectValue(networkingSummary.loadBalancers.value, networkingSummary.loadBalancers.ready) }
      ])}
      ${renderOverviewCard('Capacity', [
        { label: 'vCPU', value: projectValue(summary.vcpu_count || 0, stats.instancesLoaded) },
        { label: 'RAM', value: projectValue(Math.round((summary.ram_mb || 0) / 1024), stats.instancesLoaded, ' GB') },
        { label: 'Volumes', value: projectValue(summary.volume_count || 0, stats.storageLoaded) },
      ])}
      ${renderOverviewCard('Placement', [
        { label: 'Hosts', value: projectValue(placement.host_count || summary.host_count || 0, stats.quotaLoaded) },
        { label: 'Top Host', value: stats.quotaLoaded ? esc(placement.top_host || summary.top_host || '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
        { label: 'Concentration', value: stats.quotaLoaded ? (hostPct ? esc(`${hostPct.toFixed(0)}%`) : '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
    </div>
    <div class="summary-grid">
      <div class="card">
        <div class="card-title">Quota Snapshot</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Instances</span><span class="mv">${stats.quotaLoaded ? esc(`${computeQuota.instances?.used ?? '—'} / ${computeQuota.instances?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Ports</span><span class="mv">${stats.quotaLoaded ? esc(`${networkQuota.port?.used ?? '—'} / ${networkQuota.port?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Volumes</span><span class="mv">${stats.quotaLoaded ? esc(`${storageQuota.volumes?.used ?? '—'} / ${storageQuota.volumes?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Gigabytes</span><span class="mv">${stats.quotaLoaded ? esc(`${storageQuota.gigabytes?.used ?? '—'} / ${storageQuota.gigabytes?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Next Steps</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Security Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('security')">Open Security Tab</a></span></div>
          <div class="mrow"><span class="ml">Network Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('networking')">Open Networking Tab</a></span></div>
          <div class="mrow"><span class="ml">Storage Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('storage')">Open Storage Tab</a></span></div>
        </div>
      </div>
    </div>
  `;
}

function renderProjectInstances(inv) {
  const items = projectFilterRows(inv.instances || [], ['name', 'status', 'compute_host', 'project_name'], projectInventoryState.filter);
  const projectInstances = items.filter((item) => !isAmphoraInstance(item));
  const amphoraInstances = items.filter((item) => isAmphoraInstance(item));
  return `
    ${renderProjectToolbar('Instances', items.length, 'Filter instances…')}
    ${renderProjectCloneStatusCard()}
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Project Instances</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Name</th><th>Status</th><th>Flavor</th><th>Host</th><th>Networks</th><th>Boot</th><th>Action</th></tr></thead>
          <tbody>
            ${renderProjectInstanceRows(projectInstances, true, 'No cloneable instances match the current filter.')}
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Amphora</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Name</th><th>Status</th><th>Flavor</th><th>Host</th><th>Networks</th><th>Boot</th><th>Action</th></tr></thead>
          <tbody>
            ${renderProjectInstanceRows(amphoraInstances, false, 'No amphora instances match the current filter.')}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProjectNetworking(inv) {
  const networks = projectFilterRows(inv.networks || [], ['name', 'status', 'network_type'], projectInventoryState.filter);
  const routers = projectFilterRows(inv.routers || [], ['name', 'status', 'external_network_name', 'admin_state'], projectInventoryState.filter);
  const ports = projectFilterRows(inv.ports || [], ['name', 'status', 'network_name', 'device_owner', 'attached_name', 'attached_id', 'connected_router_name', 'mac_address'], projectInventoryState.filter);
  const floatingIps = projectFilterRows(inv.floating_ips || [], ['floating_ip_address', 'fixed_ip_address', 'status', 'description', 'instance_name', 'instance_id'], projectInventoryState.filter);
  const loadBalancers = projectFilterRows(inv.load_balancers || [], ['name', 'vip_address', 'floating_ip', 'operating_status'], projectInventoryState.filter);
  const counts = {
    networks: networks.length,
    routers: routers.length,
    ports: ports.length,
    floatingips: floatingIps.length,
    loadbalancers: loadBalancers.length,
  };
  const activeCount = counts[activeProjectNetworkingView] || 0;
  const loadingStates = {
    networks: isProjectNetworkingFamilyLoading(selectedProjectId, 'networks'),
    routers: isProjectNetworkingFamilyLoading(selectedProjectId, 'routers'),
    ports: isProjectNetworkingFamilyLoading(selectedProjectId, 'ports'),
    floatingips: isProjectNetworkingFamilyLoading(selectedProjectId, 'floating_ips'),
    loadbalancers: isProjectNetworkingFamilyLoading(selectedProjectId, 'load_balancers'),
  };
  const activeFamily = projectNetworkingFamilyKey();
  const loadingFamily = isProjectNetworkingFamilyLoading(selectedProjectId, activeFamily);
  const toolbarExtra = loadingFamily ? '<span class="hint"><span class="spinner">⟳</span> Loading current view…</span>' : '';
  const navLabel = (icon, label, key, count) => `${icon} ${label} <span>${loadingStates[key] ? '<span class="spinner">⟳</span>' : count}</span>`;
  const filterActive = Boolean(String(projectInventoryState.filter || '').trim());
  const familyEmptyState = (colspan, loadedItems, label) => {
    if (loadingFamily && !loadedItems.length) {
      return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px"><span class="spinner">⟳</span> Loading ${label}…</td></tr>`;
    }
    if (filterActive) {
      return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px">No ${label} match the current filter.</td></tr>`;
    }
    return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px">No ${label} found for this project.</td></tr>`;
  };
  const familyNav = `
    <div class="project-subnav">
      <button class="project-subnav-item ${activeProjectNetworkingView === 'networks' ? 'active' : ''}" onclick="switchProjectNetworkingView('networks')">${navLabel('🌐', 'Networks', 'networks', counts.networks)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'routers' ? 'active' : ''}" onclick="switchProjectNetworkingView('routers')">${navLabel('🛣️', 'Routers', 'routers', counts.routers)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'ports' ? 'active' : ''}" onclick="switchProjectNetworkingView('ports')">${navLabel('🔌', 'Ports', 'ports', counts.ports)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'floatingips' ? 'active' : ''}" onclick="switchProjectNetworkingView('floatingips')">${navLabel('📡', 'Floating IPs', 'floatingips', counts.floatingips)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'loadbalancers' ? 'active' : ''}" onclick="switchProjectNetworkingView('loadbalancers')">${navLabel('⚖️', 'Load Balancers', 'loadbalancers', counts.loadbalancers)}</button>
    </div>`;
  let table = '';
  if (activeProjectNetworkingView === 'networks') {
    table = `
      <div class="card">
        <div class="card-title">Networks</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Type</th><th>Subnets</th><th>Router</th></tr></thead>
            <tbody>
              ${networks.map((item) => `<tr class="${projectDetailState.kind === 'networks' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('networks','${escAttr(item.id)}')">
                <td>${esc(item.name || item.id)}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(item.network_type || '—')}</td><td>${esc(String(item.subnet_count ?? 0))}</td><td>${item.router_id ? renderObjectLink(item.router_id, `navigateToRouterDetail('${escAttr(item.router_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
              </tr>`).join('') || familyEmptyState(5, inv.networks || [], 'networks')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'routers') {
    table = `
      <div class="card">
        <div class="card-title">Routers</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Admin</th><th>External Network</th><th>Interfaces</th><th>Routes</th></tr></thead>
            <tbody>
              ${routers.map((item) => `<tr class="${projectDetailState.kind === 'routers' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('routers','${escAttr(item.id)}')">
                <td>${renderObjectLink(item.name || item.id, `selectProjectDetailById('routers','${escAttr(item.id)}')`)}</td>
                <td>${esc(item.status || 'UNKNOWN')}</td>
                <td>${esc(item.admin_state || '—')}</td>
                <td>${esc(item.external_network_name || item.external_network_id || '—')}</td>
                <td>${esc(String(item.interface_count || 0))}</td>
                <td>${esc(String(item.route_count || 0))}</td>
              </tr>`).join('') || familyEmptyState(6, inv.routers || [], 'routers')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'ports') {
    table = `
      <div class="card">
        <div class="card-title">Ports</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Network</th><th>Fixed IPs</th><th>Attached</th><th>Router</th><th>Floating IPs</th></tr></thead>
            <tbody>
              ${ports.map((item) => `<tr class="${projectDetailState.kind === 'ports' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('ports','${escAttr(item.id)}')">
                <td><div>${esc(item.name || item.id)}</div><div class="uuid-short">${esc(item.id || '')}</div></td>
                <td>${esc(item.status || 'UNKNOWN')}</td>
                <td>${item.network_id ? renderObjectLink(item.network_name || item.network_id, `navigateToNetworkDetail('${escAttr(item.network_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
                <td style="font-family:monospace;font-size:10px">${esc((item.fixed_ip_addresses || []).join(', ') || '—')}</td>
                <td>${item.attached_kind === 'instance' && item.attached_id && item.compute_host
                  ? renderObjectLink(item.attached_name || item.attached_id, `navigateToInstanceDetail('${escAttr(item.attached_id)}','${escAttr(item.compute_host)}')`)
                  : item.attached_kind === 'router' && item.attached_id
                    ? renderObjectLink(item.attached_name || item.attached_id, `navigateToRouterDetail('${escAttr(item.attached_id)}')`)
                    : item.attached_kind === 'load-balancer' && item.attached_id
                      ? renderObjectLink(item.attached_name || item.attached_id, `navigateToLoadBalancerDetail('${escAttr(item.attached_id)}')`)
                      : esc(item.attached_name || item.attached_id || '—')}</td>
                <td>${item.connected_router_id ? renderObjectLink(item.connected_router_name || item.connected_router_id, `navigateToRouterDetail('${escAttr(item.connected_router_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
                <td style="font-family:monospace;font-size:10px">${esc((item.floating_ips || []).map((ip) => ip.address).join(', ') || '—')}</td>
              </tr>`).join('') || familyEmptyState(7, inv.ports || [], 'ports')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'floatingips') {
    table = `
      <div class="card">
        <div class="card-title">Floating IPs</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Floating IP</th><th>Fixed IP</th><th>Status</th><th>Network</th><th>Instance</th><th>Port</th></tr></thead>
            <tbody>
              ${floatingIps.map((item) => `<tr class="${projectDetailState.kind === 'floatingips' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('floatingips','${escAttr(item.id)}')">
                <td>${esc(item.floating_ip_address || '—')}</td><td>${esc(item.fixed_ip_address || '—')}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(item.floating_network_name || item.floating_network_id || '—')}</td><td>${item.instance_id && item.compute_host ? renderObjectLink(item.instance_name || item.instance_id, `navigateToInstanceDetail('${escAttr(item.instance_id)}','${escAttr(item.compute_host)}')`) : esc(item.instance_name || '—')}</td><td>${item.port_id ? renderObjectLink((item.port_id || '').slice(0, 12), `navigateToPortDetail('${escAttr(item.port_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
              </tr>`).join('') || familyEmptyState(6, inv.floating_ips || [], 'floating IPs')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else {
    table = `
      <div class="card">
        <div class="card-title">Load Balancers</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>VIP</th><th>Floating IP</th><th>Status</th><th>Pools</th></tr></thead>
            <tbody>
              ${loadBalancers.map((item) => `<tr class="${projectDetailState.kind === 'loadbalancers' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('loadbalancers','${escAttr(item.id)}')">
                <td>${esc(item.name || item.id)}</td><td>${esc(item.vip_address || '—')}</td><td>${esc(item.floating_ip || '—')}</td><td>${esc(item.operating_status || 'UNKNOWN')} / ${esc(item.provisioning_status || 'UNKNOWN')}</td><td>${esc(String(item.pool_count || 0))}</td>
              </tr>`).join('') || familyEmptyState(5, inv.load_balancers || [], 'load balancers')}
            </tbody>
          </table>
        </div>
      </div>`;
  }
  return `
    ${renderProjectToolbar('Networking', activeCount, 'Filter networking…', toolbarExtra)}
    ${familyNav}
    ${table}
  `;
}

function renderProjectStorage(inv) {
  const items = projectFilterRows(inv.volumes || [], ['name', 'status', 'volume_type', 'backend_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Storage', items.length, 'Filter volumes…')}
    <div class="project-subnav">
      <button class="project-subnav-item ${activeProjectStorageView === 'volumes' ? 'active' : ''}" onclick="switchProjectStorageView('volumes')">💿 Volumes <span>${items.length}</span></button>
      <button class="project-subnav-item future" disabled>📸 Snapshots <span>Later</span></button>
      <button class="project-subnav-item future" disabled>🛟 Backups <span>Later</span></button>
    </div>
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Type</th><th>Backend</th><th>Attached</th></tr></thead>
      <tbody>
        ${items.map((item) => `<tr class="${projectDetailState.kind === 'volumes' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('volumes','${escAttr(item.id)}')">
          <td>${esc(item.name || item.id)}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(String(item.size_gb || 0))} GB</td><td>${esc(item.volume_type || '—')}</td><td>${esc(item.backend_name || '—')}</td><td>${esc(String((item.attached_to || []).length || 0))}</td>
        </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:20px">No volumes match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectSecurity(inv) {
  const items = projectFilterRows(inv.security_groups || [], ['name', 'project_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Security', items.length, 'Filter security groups…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Severity</th><th>Rules</th><th>Attached Ports</th><th>Instances</th></tr></thead>
      <tbody>
        ${items.map((item) => `<tr class="${projectDetailState.kind === 'securitygroups' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('securitygroups','${escAttr(item.id)}')">
          <td>${esc(item.name || item.id)}</td><td>${esc(item.audit?.severity || 'unknown')}</td><td>${esc(String(item.rule_count || 0))}</td><td>${esc(String(item.attachment_port_count || 0))}</td><td>${esc(String(item.attachment_instance_count || 0))}</td>
        </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No security groups match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderQuotaSection(title, entries) {
  const rows = Object.entries(entries || {});
  const editable = Boolean(authInfo?.is_admin);
  return `
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">${esc(title)}</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Resource</th><th>Used</th><th>Limit</th>${editable ? '<th>Action</th>' : ''}</tr></thead>
          <tbody>
            ${rows.map(([key, value]) => {
              const section = title === 'Compute Quotas' ? 'compute' : title === 'Network Quotas' ? 'network' : 'block_storage';
              const editing = editable && projectQuotaEditState.section === section && projectQuotaEditState.resource === key;
              const wasUpdated = projectQuotaEditState.successKey === `${section}:${key}`;
              const limitCell = editing
                ? `<div class="project-quota-editor"><input class="project-quota-input" type="text" value="${escAttr(projectQuotaEditState.value || '')}" oninput="updateProjectQuotaEditorValue(this.value)" placeholder="Enter new limit">${projectQuotaEditState.error ? `<div class="project-quota-error">${esc(projectQuotaEditState.error)}</div>` : ''}</div>`
                : `<span class="${wasUpdated ? 'project-quota-value project-quota-value-updated' : 'project-quota-value'}">${esc(String(value.limit ?? '—'))}</span>`;
              const actionCell = editable
                ? editing
                  ? `<div class="project-quota-actions"><button class="btn primary" ${projectQuotaEditState.saving ? 'disabled' : ''} onclick="saveProjectQuotaEdit()">${projectQuotaEditState.saving ? 'Saving…' : 'Save'}</button><button class="btn" ${projectQuotaEditState.saving ? 'disabled' : ''} onclick="resetProjectQuotaEditor();renderProjectsWorkspace()">Cancel</button></div>`
                  : `<div class="project-quota-actions"><button class="btn" onclick="openProjectQuotaEditor('${escAttr(section)}','${escAttr(key)}','${escAttr(value.limit ?? '')}')">Modify</button>${wasUpdated ? '<span class="project-quota-saved">Saved</span>' : ''}</div>`
                : '';
              return `<tr><td>${esc(key)}</td><td>${esc(String(value.used ?? '—'))}</td><td>${limitCell}</td>${editable ? `<td>${actionCell}</td>` : ''}</tr>`;
            }).join('') || `<tr><td colspan="${editable ? '4' : '3'}" style="text-align:center;color:var(--dim);padding:16px">No quota data available.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProjectQuota(inv) {
  const summary = projectSummary(inv.summary, inv);
  const stats = projectOverviewStats(summary.project_id || selectedProjectId);
  const quotas = inv.quotas || stats.quotas || {};
  const placement = inv.placement || stats.placement || {};
  const hostPct = Number(placement.top_host_pct || summary.top_host_pct || 0);
  const networkingSummary = projectNetworkingSummary(summary, stats);
  return `
    ${renderProjectSectionToolbar('Quota + Capacity')}
    <div class="summary-grid" style="margin-bottom:10px">
      ${renderOverviewCard('Instance Footprint', [
        { label: 'Instances', value: projectValue(summary.instance_count || 0, stats.instancesLoaded) },
        { label: 'vCPU', value: projectValue(summary.vcpu_count || 0, stats.instancesLoaded) },
        { label: 'RAM', value: projectValue(Math.round((summary.ram_mb || 0) / 1024), stats.instancesLoaded, ' GB') },
      ])}
      ${renderOverviewCard('Placement Spread', [
        { label: 'Hosts', value: projectValue(placement.host_count || summary.host_count || 0, stats.quotaLoaded) },
        { label: 'Top Host', value: stats.quotaLoaded ? esc(placement.top_host || summary.top_host || '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
        { label: 'Concentration', value: stats.quotaLoaded ? (hostPct ? esc(`${hostPct.toFixed(0)}%`) : '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
      ${renderOverviewCard('Project Networking', [
        { label: 'Networks', value: projectValue(networkingSummary.networks.value, networkingSummary.networks.ready) },
        { label: 'Routers', value: projectValue(networkingSummary.routers.value, networkingSummary.routers.ready) },
        { label: 'Ports', value: projectValue(networkingSummary.ports.value, networkingSummary.ports.ready) },
        { label: 'Floating IPs', value: projectValue(networkingSummary.floatingIps.value, networkingSummary.floatingIps.ready) },
        { label: 'Load Balancers', value: projectValue(networkingSummary.loadBalancers.value, networkingSummary.loadBalancers.ready) }
      ])}
    </div>
    ${renderQuotaSection('Compute Quotas', quotas.compute || {})}
    ${renderQuotaSection('Network Quotas', quotas.network || {})}
    ${renderQuotaSection('Block Storage Quotas', quotas.block_storage || {})}
  `;
}

function renderProjectMainContent() {
  const wrap = document.getElementById('projects-content-inner');
  if (!wrap) return;
  if (!hasOpenStackAuth()) {
    wrap.innerHTML = renderOpenStackUnavailablePanel('Projects', 'This view relies on OpenStack project-scoped inventory. Provide OpenStack credentials to enable it.');
    return;
  }

  const selected = projectSelectedRecord();
  if (!selected) {
    if (projectsState.loading && !(projectsState.data || []).length) {
      wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading projects…</div>`;
      return;
    }
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Select a project from the navigator.</div>`;
    return;
  }
  const inv = projectInventoryForView(activeProjectView);
  const activeSection = projectSectionKey(activeProjectView);
  const activeLoading = activeSection === 'networking'
    ? isProjectNetworkingFamilyLoading(selected.project_id, projectNetworkingFamilyKey())
    : isProjectSectionLoading(selected.project_id, activeSection);
  if (activeLoading && !inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading project data…</div>`;
    return;
  }
  if (!inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Project data is unavailable. Try refreshing once.</div>`;
    return;
  }

  const summary = projectSummary(inv.summary, inv);
  const detailsLoading = projectHasBackgroundLoading(selected.project_id, activeSection);
  const body = activeProjectView === 'overview'
    ? renderProjectOverview(inv)
    : activeProjectView === 'instances'
      ? renderProjectInstances(inv)
      : activeProjectView === 'networking'
        ? renderProjectNetworking(inv)
        : activeProjectView === 'storage'
          ? renderProjectStorage(inv)
          : activeProjectView === 'security'
            ? renderProjectSecurity(inv)
            : renderProjectQuota(inv);

  wrap.innerHTML = `
    <div id="obj-header">
      <div class="obj-icon">▣</div>
      <div class="obj-meta">
        <h2>${esc(summary.project_name || summary.project_id)}</h2>
        <div class="obj-sub">${esc(summary.project_id || '')}</div>
        ${summary.description ? `<div class="obj-sub">${esc(summary.description)}</div>` : ''}
      </div>
    </div>
    <div id="tabs-bar">
      <div class="tab ${activeProjectView === 'overview' ? 'active' : ''}" onclick="switchProjectSection('overview')">Overview</div>
      <div class="tab ${activeProjectView === 'instances' ? 'active' : ''}" onclick="switchProjectSection('instances')">Instances</div>
      <div class="tab ${activeProjectView === 'networking' ? 'active' : ''}" onclick="switchProjectSection('networking')">Networking</div>
      <div class="tab ${activeProjectView === 'storage' ? 'active' : ''}" onclick="switchProjectSection('storage')">Storage</div>
      <div class="tab ${activeProjectView === 'security' ? 'active' : ''}" onclick="switchProjectSection('security')">Security</div>
      <div class="tab ${activeProjectView === 'quota' ? 'active' : ''}" onclick="switchProjectSection('quota')">Quota + Capacity</div>
    </div>
    <div class="project-body-shell">
      <div class="project-body-pane">${body}</div>
      ${detailsLoading ? '<div class="project-loading-overlay"><span class="spinner">⟳</span> Loading details…</div>' : ''}
    </div>
  `;
}

function renderProjectDetailDrawer() {
  const wrap = document.getElementById('projects-detail-wrap');
  const resizer = document.getElementById('projects-detail-resizer');
  if (!wrap) return;
  if (!projectDetailState.kind || !projectDetailState.item) {
    wrap.classList.remove('open');
    resizer?.classList.remove('open');
    wrap.innerHTML = '';
    return;
  }
  wrap.classList.add('open');
  resizer?.classList.add('open');
  const item = projectDetailState.item;
  if (projectDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="net-detail-head"><strong>Detail</strong><button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;renderProjectsWorkspace()">✕</button></div><div style="padding:12px;color:var(--dim)"><span class="spinner">⟳</span> Loading detail…</div></div>`;
    return;
  }
  if (projectDetailState.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="net-detail-head"><strong>Detail</strong><button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;renderProjectsWorkspace()">✕</button></div><div class="err-block" style="margin:12px">${esc(projectDetailState.error)}</div></div>`;
    return;
  }
  const detail = projectDetailState.data || item;
  let body = '';
  if (projectDetailState.kind === 'instances' && detail) {
    const nodeName = findNodeNameForHypervisor(detail.compute_host);
    body = renderInstanceDetailContent(detail, {
      nodeName,
      instanceId: detail.id,
      expandedPortId: item.expandedPortId || '',
      portToggleJs: (portId) => `toggleProjectPortDetail('${escAttr(detail.id)}','${escAttr(portId)}')`,
    });
  } else if (projectDetailState.kind === 'volumes') {
    body = `<div class="card"><div class="card-title">Volume</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Size</span><span class="mv">${esc(String(detail.size_gb || 0))} GB</span></div>
      <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(detail.volume_type || detail.volume_type_detail?.name || '—')}</span></div>
      <div class="mrow"><span class="ml">QoS</span><span class="mv">${esc(detail.qos_policy?.name || detail.volume_type_detail?.qos_specs?.name || '—')}</span></div>
      <div class="mrow"><span class="ml">Backend</span><span class="mv">${esc(detail.backend_name || detail.volume_type_detail?.backend_name || '—')}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'networks') {
    body = `<div class="card"><div class="card-title">Network</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(item.name || item.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(item.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(item.network_type || '—')}</span></div>
      <div class="mrow"><span class="ml">Subnets</span><span class="mv">${esc(String((detail.subnets || []).length || item.subnet_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'routers') {
    body = `<div class="card"><div class="card-title">Router</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Admin</span><span class="mv">${esc(detail.admin_state || '—')}</span></div>
      <div class="mrow"><span class="ml">External Network</span><span class="mv">${esc(detail.external_network_name || detail.external_network_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Interfaces</span><span class="mv">${esc(String(detail.interface_count || 0))}</span></div>
      <div class="mrow"><span class="ml">Routes</span><span class="mv">${esc(String(detail.route_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'ports') {
    body = `<div class="card"><div class="card-title">Port</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Network</span><span class="mv">${detail.network_id ? renderObjectLink(detail.network_name || detail.network_id, `navigateToNetworkDetail('${escAttr(detail.network_id)}')`) : '—'}</span></div>
      <div class="mrow"><span class="ml">Attached</span><span class="mv">${detail.attached_kind === 'instance' && detail.attached_id && detail.compute_host
        ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToInstanceDetail('${escAttr(detail.attached_id)}','${escAttr(detail.compute_host)}')`)
        : detail.attached_kind === 'router' && detail.attached_id
          ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToRouterDetail('${escAttr(detail.attached_id)}')`)
          : detail.attached_kind === 'load-balancer' && detail.attached_id
            ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToLoadBalancerDetail('${escAttr(detail.attached_id)}')`)
            : esc(detail.attached_name || detail.attached_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Router</span><span class="mv">${detail.connected_router_id ? renderObjectLink(detail.connected_router_name || detail.connected_router_id, `navigateToRouterDetail('${escAttr(detail.connected_router_id)}')`) : '—'}</span></div>
      <div class="mrow"><span class="ml">Fixed IPs</span><span class="mv mono">${esc((detail.fixed_ip_addresses || []).join(', ') || '—')}</span></div>
      <div class="mrow"><span class="ml">Floating IPs</span><span class="mv mono">${esc((detail.floating_ips || []).map((ip) => ip.address).join(', ') || '—')}</span></div>
      <div class="mrow"><span class="ml">Security Groups</span><span class="mv">${(detail.security_groups || []).length ? (detail.security_groups || []).map((group) => renderObjectLink(group.name || group.id, `switchNetworkingSection('securitygroups');selectSecurityGroup('${escAttr(group.id)}')`)).join(', ') : '—'}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'securitygroups') {
    body = `<div class="card"><div class="card-title">Security Group</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Severity</span><span class="mv">${esc(detail.audit?.severity || 'unknown')}</span></div>
      <div class="mrow"><span class="ml">Rules</span><span class="mv">${esc(String(detail.rule_count || 0))}</span></div>
      <div class="mrow"><span class="ml">Instances</span><span class="mv">${esc(String(detail.attachment_instance_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'floatingips') {
    body = `<div class="card"><div class="card-title">Floating IP</div><div class="card-body">
      <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(detail.floating_ip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Fixed IP</span><span class="mv">${esc(detail.fixed_ip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Network</span><span class="mv">${esc(detail.floating_network_name || detail.floating_network_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Instance</span><span class="mv">${detail.instance_id && detail.compute_host ? renderObjectLink(detail.instance_name || detail.instance_id, `navigateToInstanceDetail('${escAttr(detail.instance_id)}','${escAttr(detail.compute_host)}')`) : esc(detail.instance_name || '—')}</span></div>
      <div class="mrow"><span class="ml">Port</span><span class="mv">${detail.port_id ? renderObjectLink(detail.port_id, `navigateToPortDetail('${escAttr(detail.port_id)}')`) : '—'}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'loadbalancers') {
    body = `<div class="card"><div class="card-title">Load Balancer</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">VIP</span><span class="mv">${esc(detail.vip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(detail.floating_ip || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.operating_status || 'UNKNOWN')} / ${esc(detail.provisioning_status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Pools</span><span class="mv">${esc(String((detail.pools || []).length || detail.pool_count || 0))}</span></div>
    </div></div>`;
  }
  wrap.innerHTML = `
    <div class="net-detail-inner">
      <div class="net-detail-head">
        <strong style="font-size:13px">${esc(projectDetailState.kind === 'securitygroups' ? 'Security Group' : projectDetailState.kind === 'floatingips' ? 'Floating IP' : projectDetailState.kind === 'loadbalancers' ? 'Load Balancer' : projectDetailState.kind === 'networks' ? 'Network' : projectDetailState.kind === 'routers' ? 'Router' : projectDetailState.kind === 'ports' ? 'Port' : projectDetailState.kind === 'volumes' ? 'Volume' : 'Instance')} Detail</strong>
        <div style="display:flex;gap:6px;align-items:center">
          ${projectDetailState.kind === 'instances' && !isAmphoraInstance(detail || item) ? `<button class="btn" style="padding:1px 7px;font-size:11px" onclick="cloneProjectInstance('${escAttr(detail.id || item.id || '')}')" ${(isProjectCloneActive() || projectCloneState.submitting) ? `disabled title="Clone already running for ${escAttr(projectCloneState.activeCloneLabel || 'another instance')}"` : ''}>${(isProjectCloneActive() || projectCloneState.submitting) ? 'Clone Busy…' : 'Clone'}</button>` : ''}
          <button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;projectDetailState.data=null;projectDetailState.error=null;renderProjectsWorkspace()">✕</button>
        </div>
      </div>
      ${body}
    </div>
  `;
}

function renderProjectsWorkspace() {
  renderProjectsSidebar();
  renderProjectMainContent();
  renderProjectDetailDrawer();
}
