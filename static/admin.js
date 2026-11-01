// Admin site: overview and reports, chat history, settings

const $ = (id) => document.getElementById(id);

let allCases = [];
let shownCases = [];
let openCaseKey = null;
let modelSettings = null;
let availableModels = [];
let aiPasswordValue = '';
let aiPasswordSource = '';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('Not logged in');
    }
    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
            detail = (await response.json()).detail || detail;
        } catch (e) { /* not JSON */ }
        throw new Error(detail);
    }
    return response;
}

function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// Save a fetch response as a file, using the name the server sent
async function download(response, fallbackName) {
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/);
    const plain = disposition.match(/filename="([^"]+)"/);
    const name = utf8 ? decodeURIComponent(utf8[1]) : (plain ? plain[1] : fallbackName);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

async function withBusy(button, label, task) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
        await task();
    } finally {
        button.disabled = false;
        button.textContent = original;
    }
}

// ---------------------------------------------------------------------------
// Tabs and account
// ---------------------------------------------------------------------------
function showTab(name) {
    document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
    document.querySelectorAll('.panel').forEach((panel) => { panel.hidden = panel.id !== `tab-${name}`; });
    if (name === 'overview') loadSummary();
    if (name === 'history') loadCases();
    if (name === 'settings') loadSettings();
}

async function loadMe() {
    const user = await (await api('/api/me')).json();
    $('currentUser').textContent = user.username;
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login';
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function loadSummary() {
    try {
        const data = await (await api('/api/admin/summary')).json();
        $('statTotal').textContent = data.total;
        $('statAgents').textContent = data.agents;
        $('statAiOn').textContent = data.ai_enabled;
        $('statAiOff').textContent = data.ai_disabled;
        $('statLatest').textContent = `Latest evaluation: ${data.latest || '-'}`;
    } catch (error) {
        toast(`Could not load summary: ${error.message}`, true);
    }
}

async function downloadReport(button, aiEnabled) {
    await withBusy(button, 'Creating report...', async () => {
        try {
            await download(await api(`/api/admin/generate-reports/${aiEnabled}`), 'agent_reports.zip');
        } catch (error) {
            toast(error.message === 'No score data found' ? 'No evaluated cases in this group yet' : error.message, true);
        }
    });
}

// ---------------------------------------------------------------------------
// Chat history
// ---------------------------------------------------------------------------
async function loadCases() {
    try {
        allCases = (await (await api('/api/admin/cases')).json()).cases;
        renderCases();
    } catch (error) {
        toast(`Could not load chat history: ${error.message}`, true);
    }
}

function filteredCases() {
    const text = $('filterText').value.trim().toLowerCase();
    const ai = $('filterAi').value;
    const from = $('filterFrom').value;
    const to = $('filterTo').value;
    return allCases.filter((c) => {
        const date = (c.evaluation_date || '').slice(0, 10);
        if (ai === 'on' && !c.ai_enabled) return false;
        if (ai === 'off' && c.ai_enabled) return false;
        if (from && date < from) return false;
        if (to && date > to) return false;
        if (text) {
            const haystack = [c.agent_name, c.domain, c.job_position].join(' ').toLowerCase();
            if (!haystack.includes(text)) return false;
        }
        return true;
    });
}

function renderCases() {
    shownCases = filteredCases();
    const body = $('caseRows');
    body.innerHTML = '';
    shownCases.forEach((c) => {
        const row = document.createElement('tr');
        row.dataset.key = c.key;

        const check = el('td');
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.className = 'row-check';
        box.value = c.key;
        box.setAttribute('aria-label', `Select ${c.agent_name}`);
        box.addEventListener('click', (event) => event.stopPropagation());
        box.addEventListener('change', updateSelection);
        check.appendChild(box);
        row.appendChild(check);

        row.appendChild(el('td', '', c.evaluation_date || '-'));
        row.appendChild(el('td', '', c.agent_name));
        const ai = el('td');
        ai.appendChild(el('span', c.ai_enabled ? 'badge on' : 'badge', c.ai_enabled ? 'On' : 'Off'));
        row.appendChild(ai);
        const scenario = [c.domain, c.job_position].filter(Boolean).join(' / ');
        row.appendChild(el('td', 'scenario', scenario || '-'));
        row.appendChild(el('td', '', c.difficulty || '-'));
        row.appendChild(el('td', 'num', c.average_score.toFixed(2)));
        row.appendChild(el('td', 'num', c.total_time_minutes.toFixed(1)));
        row.appendChild(el('td', 'num', String(c.message_count)));

        row.addEventListener('click', () => openCase(c.key));
        body.appendChild(row);
    });
    $('casesEmpty').hidden = shownCases.length > 0;
    $('caseCount').textContent = `${shownCases.length} of ${allCases.length} cases`;
    $('exportShown').disabled = shownCases.length === 0;
    $('selectAll').checked = false;
    updateSelection();
}

function selectedKeys() {
    return Array.from(document.querySelectorAll('.row-check:checked')).map((box) => box.value);
}

function updateSelection() {
    const count = selectedKeys().length;
    $('exportSelected').disabled = count === 0;
    $('exportSelected').textContent = count ? `Export selected (${count})` : 'Export selected (Excel)';
}

async function exportCases(button, keys) {
    await withBusy(button, 'Exporting...', async () => {
        try {
            const response = await api('/api/admin/cases/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keys })
            });
            await download(response, 'chat_history.xlsx');
        } catch (error) {
            toast(`Export failed: ${error.message}`, true);
        }
    });
}

const SCORE_LABELS = {
    response_accuracy: 'Accuracy',
    response_time: 'Response time',
    empathy_and_tone: 'Empathy and tone',
    clarity: 'Clarity',
    professionalism: 'Professionalism'
};

async function openCase(key) {
    let data;
    try {
        data = await (await api(`/api/admin/cases/${encodeURIComponent(key)}`)).json();
    } catch (error) {
        toast(`Could not open chat: ${error.message}`, true);
        return;
    }
    openCaseKey = key;
    $('caseTitle').textContent = data.agent_name;
    const meta = [
        data.evaluation_date,
        `AI ${data.ai_enabled ? 'enabled' : 'disabled'}`,
        `${data.total_time_minutes.toFixed(1)} min`,
        [data.config.domain, data.config.job_position, data.config.difficulty, data.config.language].filter(Boolean).join(' / ')
    ].filter(Boolean);
    $('caseMeta').textContent = meta.join('  |  ');

    const scores = $('caseScores');
    scores.innerHTML = '';
    Object.entries(data.scores).forEach(([name, value]) => {
        const box = el('div', 'case-score');
        box.appendChild(el('strong', '', `${value}/5`));
        box.appendChild(el('span', '', SCORE_LABELS[name] || name));
        scores.appendChild(box);
    });
    const avg = el('div', 'case-score');
    avg.appendChild(el('strong', '', data.average_score.toFixed(2)));
    avg.appendChild(el('span', '', 'Average'));
    scores.appendChild(avg);

    $('caseComment').textContent = data.comment || '';

    const conversation = $('caseConversation');
    conversation.innerHTML = '';
    if (!data.messages.length) {
        conversation.appendChild(el('p', 'muted', 'No messages saved for this case.'));
    }
    data.messages.forEach((m) => {
        const isAgent = m.role === 'user';
        const bubble = el('div', `bubble ${isAgent ? 'agent' : 'customer'}`);
        const time = m.timestamp ? ` ${m.timestamp.slice(11, 19)}` : '';
        bubble.appendChild(el('span', 'who', `${isAgent ? `Agent (${data.agent_name})` : 'Customer (bot)'}${time}`));
        bubble.appendChild(document.createTextNode(m.content));
        conversation.appendChild(bubble);
    });

    $('drawerBackdrop').hidden = false;
    $('caseDrawer').hidden = false;
    $('caseDrawer').scrollTop = 0;
}

function closeCase() {
    $('drawerBackdrop').hidden = true;
    $('caseDrawer').hidden = true;
    openCaseKey = null;
}

async function exportCase(button, format) {
    if (!openCaseKey) return;
    await withBusy(button, 'Exporting...', async () => {
        try {
            const url = `/api/admin/cases/${encodeURIComponent(openCaseKey)}/export?format=${format}`;
            await download(await api(url), `${openCaseKey}.${format}`);
        } catch (error) {
            toast(`Export failed: ${error.message}`, true);
        }
    });
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
const SOURCE_TEXT = {
    admin: 'set on this admin site',
    environment: 'from deployment (Secret Manager)',
    none: 'not set'
};

function renderSettings(data) {
    const key = data.gemini_api_key;
    $('keyStatus').textContent = key.set ? `${key.masked} (${SOURCE_TEXT[key.source]})` : 'Not set, the chat will not work';
    $('keyClear').hidden = key.source !== 'admin';
    modelSettings = data.models;
    renderModelSelects();

    aiPasswordValue = data.ai_password.value || '';
    aiPasswordSource = SOURCE_TEXT[data.ai_password.source];
    $('aiPwShow').hidden = !aiPasswordValue;
    showAiPassword(false);
    $('aiPwClear').hidden = data.ai_password.source !== 'admin';

    $('settingsUpdated').textContent = data.updated_at
        ? `Last changed by ${data.updated_by} at ${data.updated_at}. Changes reach every server within 30 seconds.`
        : '';
}

async function loadSettings() {
    try {
        renderSettings(await (await api('/api/admin/settings')).json());
    } catch (error) {
        toast(`Could not load settings: ${error.message}`, true);
        return;
    }
    loadModels();
}

// Fill one model dropdown: deployment default first, then the models the key can use.
// A saved model that is not in the list (for example when the list cannot load) stays selectable.
function fillModelSelect(select, current, fromAdmin, deploymentModel) {
    select.innerHTML = '';
    const def = document.createElement('option');
    def.value = '';
    def.textContent = `Deployment default (${deploymentModel})`;
    select.appendChild(def);
    const names = availableModels.map((m) => m.name);
    if (fromAdmin && !names.includes(current)) {
        names.unshift(current);
    }
    names.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        const model = availableModels.find((m) => m.name === name);
        option.textContent = model && model.display_name !== name ? `${name} (${model.display_name})` : name;
        select.appendChild(option);
    });
    select.value = fromAdmin ? current : '';
}

function renderModelSelects() {
    if (!modelSettings) return;
    fillModelSelect($('chatModel'), modelSettings.chat, modelSettings.chat_from_admin, modelSettings.deployment_chat);
    fillModelSelect($('evalModel'), modelSettings.evaluation, modelSettings.evaluation_from_admin, modelSettings.deployment_evaluation);
}

async function loadModels() {
    try {
        availableModels = (await (await api('/api/admin/models')).json()).models;
        renderModelSelects();
        setResult($('modelResult'), `${availableModels.length} models available`, true);
    } catch (error) {
        availableModels = [];
        renderModelSelects();
        setResult($('modelResult'), `Could not load the model list: ${error.message}`, false);
    }
}

async function saveModels(button) {
    await withBusy(button, 'Saving...', () => saveSettings(
        { chat_model: $('chatModel').value, eval_model: $('evalModel').value },
        $('modelResult'),
        'Saved. New chats and evaluations use these models.'
    ));
}

async function testChatModel(button) {
    const model = $('chatModel').value || modelSettings.deployment_chat;
    await withBusy(button, 'Testing...', async () => {
        try {
            const response = await api('/api/admin/settings/test-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            });
            const data = await response.json();
            setResult($('modelResult'), data.ok ? `${data.model} works` : `${data.model} did not work: ${data.detail}`, data.ok);
        } catch (error) {
            setResult($('modelResult'), error.message, false);
        }
    });
}

async function saveSettings(body, resultEl, message) {
    try {
        const response = await api('/api/admin/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        renderSettings(await response.json());
        setResult(resultEl, message, true);
        return true;
    } catch (error) {
        setResult(resultEl, error.message, false);
        return false;
    }
}

function setResult(resultEl, message, ok) {
    resultEl.textContent = message;
    resultEl.className = `result ${ok ? 'ok' : 'error'}`;
}

async function testKey(button) {
    const key = $('keyInput').value.trim();
    await withBusy(button, 'Testing...', async () => {
        try {
            const response = await api('/api/admin/settings/test-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ gemini_api_key: key || null })
            });
            const data = await response.json();
            setResult($('keyResult'), data.ok
                ? `${key ? 'This key' : 'The current key'} works with ${data.model}`
                : `Key did not work: ${data.detail}`, data.ok);
        } catch (error) {
            setResult($('keyResult'), error.message, false);
        }
    });
}

async function saveKey(button) {
    const key = $('keyInput').value.trim();
    if (!key) {
        setResult($('keyResult'), 'Paste a new key first', false);
        return;
    }
    await withBusy(button, 'Saving...', async () => {
        if (await saveSettings({ gemini_api_key: key }, $('keyResult'), 'Saved. Use "Test key" to check it.')) {
            $('keyInput').value = '';
            loadModels();
        }
    });
}

function generatePassword() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
    const values = crypto.getRandomValues(new Uint32Array(10));
    $('aiPwInput').value = Array.from(values, (v) => chars[v % chars.length]).join('');
}

async function saveAiPassword(button) {
    const value = $('aiPwInput').value.trim();
    if (!value) {
        setResult($('aiPwResult'), 'Type or generate a new password first', false);
        return;
    }
    await withBusy(button, 'Saving...', async () => {
        if (await saveSettings({ ai_password: value }, $('aiPwResult'), 'Saved. Give the new password to the agents.')) {
            $('aiPwInput').value = '';
        }
    });
}

function showAiPassword(visible) {
    $('aiPwStatus').textContent = aiPasswordValue
        ? `${visible ? aiPasswordValue : '••••••'} (${aiPasswordSource})`
        : 'Not set, agents cannot turn on AI';
    $('aiPwShow').textContent = visible ? 'Hide' : 'Show';
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
    $('logoutBtn').addEventListener('click', logout);

    $('reportAiOn').addEventListener('click', (e) => downloadReport(e.currentTarget, true));
    $('reportAiOff').addEventListener('click', (e) => downloadReport(e.currentTarget, false));

    ['filterText', 'filterAi', 'filterFrom', 'filterTo'].forEach((id) => $(id).addEventListener('input', renderCases));
    $('selectAll').addEventListener('change', (e) => {
        document.querySelectorAll('.row-check').forEach((box) => { box.checked = e.target.checked; });
        updateSelection();
    });
    $('exportSelected').addEventListener('click', (e) => exportCases(e.currentTarget, selectedKeys()));
    $('exportShown').addEventListener('click', (e) => exportCases(e.currentTarget, shownCases.map((c) => c.key)));

    $('drawerClose').addEventListener('click', closeCase);
    $('drawerBackdrop').addEventListener('click', closeCase);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCase(); });
    $('caseExportDocx').addEventListener('click', (e) => exportCase(e.currentTarget, 'docx'));
    $('caseExportTxt').addEventListener('click', (e) => exportCase(e.currentTarget, 'txt'));

    $('keyTest').addEventListener('click', (e) => testKey(e.currentTarget));
    $('keySave').addEventListener('click', (e) => saveKey(e.currentTarget));
    $('modelRefresh').addEventListener('click', (e) => withBusy(e.currentTarget, 'Loading...', loadModels));
    $('modelTest').addEventListener('click', (e) => testChatModel(e.currentTarget));
    $('modelSave').addEventListener('click', (e) => saveModels(e.currentTarget));
    $('keyClear').addEventListener('click', (e) => withBusy(e.currentTarget, 'Saving...', async () => {
        if (await saveSettings({ clear_gemini_api_key: true }, $('keyResult'), 'Now using the key from deployment')) {
            loadModels();
        }
    }));
    $('aiPwShow').addEventListener('click', () => showAiPassword($('aiPwShow').textContent === 'Show'));
    $('aiPwGenerate').addEventListener('click', generatePassword);
    $('aiPwSave').addEventListener('click', (e) => saveAiPassword(e.currentTarget));
    $('aiPwClear').addEventListener('click', (e) => withBusy(e.currentTarget, 'Saving...', () =>
        saveSettings({ clear_ai_password: true }, $('aiPwResult'), 'Now using the password from deployment')));

    loadMe();
    showTab('overview');
});
