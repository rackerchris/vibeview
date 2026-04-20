'use strict';

const assistantState = {
  open: false,
  loading: false,
  validating: false,
  streaming: false,
  query: '',
  resolvedChoice: null,
  result: null,
  error: '',
  status: null,
  validation: null,
};

function resetAssistantState() {
  assistantState.open = false;
  assistantState.loading = false;
  assistantState.validating = false;
  assistantState.streaming = false;
  assistantState.query = '';
  assistantState.resolvedChoice = null;
  assistantState.result = null;
  assistantState.error = '';
  assistantState.status = null;
  assistantState.validation = null;
  renderAssistantPanel();
}

function assistantContext() {
  const selected = typeof projectSelectedRecord === 'function' ? projectSelectedRecord() : null;
  return {
    selected_project_id: typeof selectedProjectId !== 'undefined' ? selectedProjectId || '' : '',
    selected_project_name: selected?.project_name || authInfo?.project_name || '',
    active_view: typeof activeView !== 'undefined' ? activeView || '' : '',
  };
}

function toggleAssistantPanel(forceOpen = null) {
  assistantState.open = typeof forceOpen === 'boolean' ? forceOpen : !assistantState.open;
  if (assistantState.open && !assistantState.status) loadAssistantStatus();
  renderAssistantPanel();
}

function setAssistantQuery(value) {
  if (assistantState.query !== value) assistantState.resolvedChoice = null;
  assistantState.query = value;
}

async function submitAssistantQuery() {
  const query = String(assistantState.query || '').trim();
  if (!query || assistantState.loading) return;
  const requestStartedAt = performance.now();
  let firstEventLogged = false;
  let firstDeltaLogged = false;
  assistantState.loading = true;
  assistantState.streaming = false;
  assistantState.error = '';
  assistantState.result = null;
  renderAssistantPanel();
  try {
    const requestPayload = {
      query,
      ...assistantContext(),
      resolved_kind: assistantState.resolvedChoice?.kind || '',
      resolved_id: assistantState.resolvedChoice?.id || '',
      resolved_label: assistantState.resolvedChoice?.label || '',
    };
    const resp = await fetch('/api/assistant/query/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });
    if (!resp.ok || !resp.body) {
      const json = await resp.json();
      if (json.api_issue) recordApiIssue(json.api_issue);
      else recordApiSuccess('Assistant');
      throw new Error(json.error || `HTTP ${resp.status}`);
    }
    console.info('[Assistant] stream response started', {
      query,
      elapsed_ms: Number((performance.now() - requestStartedAt).toFixed(1)),
      status: resp.status,
    });
    assistantState.streaming = true;
    assistantState.result = {
      answer: '',
      actions: [],
      entities: [],
      choices: [],
      findings: [],
      recommendations: [],
    };
    renderAssistantPanel();
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const event = JSON.parse(trimmed);
        if (event.api_issue) recordApiIssue(event.api_issue);
        if (!firstEventLogged) {
          firstEventLogged = true;
          console.info('[Assistant] first stream event', {
            query,
            type: event.type || 'unknown',
            elapsed_ms: Number((performance.now() - requestStartedAt).toFixed(1)),
          });
        }
        if (event.type === 'meta') {
          assistantState.result = {
            ...(assistantState.result || {}),
            ...(event.meta || {}),
            answer: assistantState.result?.answer || '',
            actions: assistantState.result?.actions || [],
            entities: assistantState.result?.entities || [],
            choices: assistantState.result?.choices || [],
            findings: assistantState.result?.findings || [],
            recommendations: assistantState.result?.recommendations || [],
          };
          renderAssistantPanel();
          continue;
        }
        if (event.type === 'delta') {
          if (!firstDeltaLogged) {
            firstDeltaLogged = true;
            console.info('[Assistant] first stream text', {
              query,
              elapsed_ms: Number((performance.now() - requestStartedAt).toFixed(1)),
            });
          }
          assistantState.result = assistantState.result || {};
          assistantState.result.answer = `${assistantState.result.answer || ''}${event.text || ''}`;
          renderAssistantPanel();
          continue;
        }
        if (event.type === 'final') {
          assistantState.result = event.result || assistantState.result;
          if (!assistantState.result?.needs_clarification) assistantState.resolvedChoice = null;
          recordApiSuccess('Assistant');
          renderAssistantPanel();
          continue;
        }
        if (event.type === 'error') throw new Error(event.error || 'Assistant streaming failed');
      }
    }
  } catch (err) {
    assistantState.error = String(err);
    assistantState.result = null;
    assistantState.resolvedChoice = null;
  } finally {
    assistantState.loading = false;
    assistantState.streaming = false;
    renderAssistantPanel();
  }
}

function submitAssistantChoice(index) {
  const choices = Array.isArray(assistantState.result?.choices) ? assistantState.result.choices : [];
  const choice = choices[index];
  if (!choice) return;
  assistantState.resolvedChoice = {
    kind: String(choice.kind || ''),
    id: String(choice.id || ''),
    label: String(choice.label || ''),
  };
  submitAssistantQuery();
}

async function loadAssistantStatus() {
  try {
    const resp = await fetch('/api/assistant/status');
    const json = await resp.json();
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    assistantState.status = json.status || null;
  } catch (err) {
    assistantState.status = { configured: false, error: String(err) };
  }
  renderAssistantPanel();
}

async function validateAssistantProvider() {
  if (assistantState.validating) return;
  assistantState.validating = true;
  assistantState.validation = null;
  assistantState.error = '';
  renderAssistantPanel();
  try {
    const resp = await fetch('/api/assistant/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Assistant');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    assistantState.validation = json.validation || null;
    assistantState.status = json.validation || assistantState.status;
  } catch (err) {
    assistantState.error = String(err);
    assistantState.validation = null;
  } finally {
    assistantState.validating = false;
    renderAssistantPanel();
  }
}

function executeAssistantAction(action) {
  if (!action || typeof action !== 'object') return;
  if (action.type === 'open_projects') {
    switchView('projects');
    return;
  }
  if (action.type === 'open_project_tab') {
    switchView('projects');
    const targetProjectId = String(action.project_id || '');
    if (targetProjectId && typeof selectProject === 'function') {
      if (projectsState?.data?.length) selectProject(targetProjectId);
      else loadProjects(true).then(() => selectProject(targetProjectId));
    }
    if (action.tab && typeof switchProjectSection === 'function') switchProjectSection(action.tab);
    return;
  }
  if (action.type === 'open_instance' && action.id && action.compute_host && typeof navigateToInstanceDetail === 'function') {
    navigateToInstanceDetail(action.id, action.compute_host);
    return;
  }
  if (action.type === 'open_networking_security_group' && action.id) {
    if (typeof switchNetworkingSection === 'function') switchNetworkingSection('securitygroups');
    if (typeof selectSecurityGroup === 'function') selectSecurityGroup(action.id);
    return;
  }
  if (action.type === 'open_project_network_family') {
    switchView('projects');
    const targetProjectId = String(action.project_id || '');
    if (targetProjectId && typeof selectProject === 'function') {
      if (projectsState?.data?.length) selectProject(targetProjectId);
      else loadProjects(true).then(() => selectProject(targetProjectId));
    }
    if (typeof switchProjectSection === 'function') switchProjectSection('networking');
    if (action.family && typeof switchProjectNetworkingView === 'function') switchProjectNetworkingView(action.family);
    return;
  }
  if (action.type === 'open_network' && action.id && typeof navigateToNetworkDetail === 'function') {
    navigateToNetworkDetail(action.id);
    return;
  }
  if (action.type === 'open_router' && action.id && typeof navigateToRouterDetail === 'function') {
    navigateToRouterDetail(action.id);
    return;
  }
  if (action.type === 'open_port' && action.id && typeof navigateToPortDetail === 'function') {
    navigateToPortDetail(action.id);
    return;
  }
  if (action.type === 'open_load_balancer' && action.id && typeof navigateToLoadBalancerDetail === 'function') {
    navigateToLoadBalancerDetail(action.id);
    return;
  }
  if (action.type === 'open_volume' && action.id) {
    switchView('storage');
    if (typeof switchStorageSection === 'function') switchStorageSection('openstack-volumes');
    if (typeof selectVolume === 'function') selectVolume(action.id);
  }
}

function renderAssistantActionButtons(result) {
  const actions = Array.isArray(result?.actions) ? result.actions : [];
  if (!actions.length) return '';
  return `
    <div class="assistant-actions">
      ${actions.map((action, index) => `<button class="btn" onclick="executeAssistantAction(assistantState.result.actions[${index}])">${esc(action.label || action.tab || action.type.replaceAll('_', ' '))}</button>`).join('')}
    </div>
  `;
}

function renderAssistantClarification(result) {
  const choices = Array.isArray(result?.choices) ? result.choices : [];
  if (!result?.needs_clarification || !choices.length) return '';
  return `
    <div class="assistant-clarify">
      <div class="assistant-clarify-title">${esc(result.clarifying_question || 'Choose one result')}</div>
      <div class="assistant-choice-list">
        ${choices.map((choice, index) => `
          <button class="assistant-choice" onclick="submitAssistantChoice(${index})">
            <span class="assistant-choice-label">${esc(choice.label || choice.id || 'Unknown')}</span>
            ${choice.subtext ? `<span class="assistant-choice-subtext">${esc(choice.subtext)}</span>` : ''}
          </button>
        `).join('')}
      </div>
    </div>
  `;
}

function renderAssistantTimings(result) {
  const timings = result?.timings;
  if (!timings || typeof timings !== 'object') return '';
  return `<span>Timing: ${esc(String(timings.total_ms ?? '—'))} ms total • ${esc(String(timings.parse_ms ?? '—'))} ms parse • ${esc(String(timings.answer_ms ?? '—'))} ms app</span>`;
}

function renderAssistantConfidence(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return esc(String(value ?? '—'));
  let label = 'Very Low';
  if (numeric >= 0.9) label = 'Very High';
  else if (numeric >= 0.75) label = 'High';
  else if (numeric >= 0.5) label = 'Medium';
  else if (numeric >= 0.25) label = 'Low';
  return `${label} (${esc(numeric.toFixed(2))})`;
}

function formatAssistantInline(text) {
  return esc(String(text || ''))
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function looksLikeAssistantCommand(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed) return false;
  if (/^(?:\$|#)\s+/.test(trimmed)) return true;
  if (/^(?:openstack|nova|neutron|cinder|glance|keystone|octavia|ovs-vsctl|ovn-nbctl|ovn-sbctl|kubectl|oc)\b/.test(trimmed)) return true;
  return false;
}

function renderAssistantAnswer(text) {
  const source = String(text || '').trim();
  if (!source) return '';
  const lines = source.split(/\r?\n/);
  const blocks = [];
  let paragraph = [];
  let list = [];
  let codeBlock = [];
  let inFence = false;

  function flushParagraph() {
    if (!paragraph.length) return;
    blocks.push(`<p>${formatAssistantInline(paragraph.join(' '))}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!list.length) return;
    blocks.push(`<ul>${list.map((item) => `<li>${formatAssistantInline(item)}</li>`).join('')}</ul>`);
    list = [];
  }

  function flushCodeBlock() {
    if (!codeBlock.length) return;
    blocks.push(`<pre class="assistant-code-block"><code>${esc(codeBlock.join('\n'))}</code></pre>`);
    codeBlock = [];
  }

  lines.forEach((rawLine) => {
    const trimmed = rawLine.trim();
    if (/^```/.test(trimmed)) {
      flushParagraph();
      flushList();
      if (inFence) {
        flushCodeBlock();
        inFence = false;
      } else {
        inFence = true;
      }
      return;
    }
    if (inFence) {
      codeBlock.push(rawLine);
      return;
    }
    const line = trimmed;
    if (!line) {
      flushParagraph();
      flushList();
      flushCodeBlock();
      return;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    const numbered = line.match(/^\d+\.\s+(.+)$/);
    if (looksLikeAssistantCommand(rawLine)) {
      flushParagraph();
      flushList();
      codeBlock.push(rawLine.replace(/^\$\s?/, ''));
      return;
    }
    if (codeBlock.length) flushCodeBlock();
    if (bullet || numbered) {
      flushParagraph();
      list.push((bullet || numbered)[1]);
      return;
    }
    if (list.length) flushList();
    paragraph.push(line);
  });

  flushParagraph();
  flushList();
  flushCodeBlock();
  return blocks.join('') || `<p>${formatAssistantInline(source)}</p>`;
}

function renderAssistantProgress(result) {
  if (!assistantState.loading) return '';
  const hasAnswerText = Boolean(String(result?.answer || '').trim());
  if (hasAnswerText) return '';
  return `<div class="assistant-progress">${assistantState.streaming ? 'Streaming response…' : 'Thinking…'}</div>`;
}

function renderAssistantDetails(result) {
  const findings = Array.isArray(result?.findings) ? result.findings : [];
  const recommendations = Array.isArray(result?.recommendations) ? result.recommendations : [];
  if (!findings.length && !recommendations.length) return '';
  return `
    <div class="assistant-clarify">
      ${findings.length ? `<div class="assistant-clarify-title">Findings</div><div class="assistant-choice-list">${findings.map((item) => `<div class="assistant-choice"><span class="assistant-choice-label">${esc(item)}</span></div>`).join('')}</div>` : ''}
      ${recommendations.length ? `<div class="assistant-clarify-title" style="margin-top:10px">Recommendations</div><div class="assistant-choice-list">${recommendations.map((item) => `<div class="assistant-choice"><span class="assistant-choice-label">${esc(item)}</span></div>`).join('')}</div>` : ''}
    </div>
  `;
}

function renderAssistantStatus() {
  const validation = assistantState.validation;
  if (!validation) return '';
  return `
    <div class="assistant-result">
      <div class="assistant-answer">${validation.ok ? 'Assistant provider validation succeeded.' : 'Assistant provider validation failed.'}</div>
      <div class="assistant-meta">
        <span>Validation: ${validation.ok ? 'ok' : 'failed'}</span>
        <span>Latency: ${esc(String(validation.latency_ms ?? '—'))} ms</span>
        <span>Reply: ${esc(validation.response_text || '—')}</span>
      </div>
    </div>
  `;
}

function renderAssistantPanel() {
  const wrap = document.getElementById('assistant-panel');
  if (!wrap) return;
  wrap.style.display = assistantState.open ? 'block' : 'none';
  if (!assistantState.open) {
    wrap.innerHTML = '';
    return;
  }
  const result = assistantState.result;
  wrap.innerHTML = `
    <div class="assistant-shell">
      <div class="assistant-head">
        <div>
          <div class="assistant-title">OpenVibe Assistant</div>
          <div class="assistant-sub">Ask about quotas, VMs, networking, storage, or security.</div>
        </div>
        <button class="btn" onclick="toggleAssistantPanel(false)">✕</button>
      </div>
      <div class="assistant-body">
        <textarea class="assistant-input" placeholder="How many floating IPs am I using?" oninput="setAssistantQuery(this.value)">${esc(assistantState.query || '')}</textarea>
        <div class="assistant-toolbar">
          <span class="hint">Scope: ${esc(assistantContext().selected_project_name || 'current session')}</span>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn" ${assistantState.validating ? 'disabled' : ''} onclick="validateAssistantProvider()">${assistantState.validating ? 'Validating…' : 'Validate Provider'}</button>
            <button class="btn primary" ${assistantState.loading ? 'disabled' : ''} onclick="submitAssistantQuery()">${assistantState.loading ? (assistantState.streaming ? 'Streaming…' : 'Thinking…') : 'Ask'}</button>
          </div>
        </div>
        ${assistantState.error ? `<div class="err-block">${esc(assistantState.error)}</div>` : ''}
        ${renderAssistantStatus()}
        ${result ? `
          <div class="assistant-result">
            ${renderAssistantProgress(result)}
            ${String(result.answer || '').trim() ? `<div class="assistant-answer">${renderAssistantAnswer(result.answer || '')}</div>` : ''}
            <div class="assistant-meta">
              <span>Intent: ${esc(result.intent || 'unknown')}</span>
              <span>Confidence: ${renderAssistantConfidence(result.confidence)}</span>
              <span>Parser: ${esc(result.parser_source || 'fallback')}</span>
              <span>Answered by: ${esc(result.answer_source || 'local')}</span>
              ${result.mode ? `<span>Mode: ${esc(result.mode)}</span>` : ''}
              ${result.provenance ? `<span>Source: ${esc(result.provenance)}</span>` : ''}
              ${renderAssistantTimings(result)}
            </div>
            ${renderAssistantDetails(result)}
            ${renderAssistantClarification(result)}
            ${renderAssistantActionButtons(result)}
          </div>
        ` : '<div class="assistant-placeholder">Answers will appear here.</div>'}
      </div>
    </div>
  `;
}
