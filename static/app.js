const $ = (selector) => document.querySelector(selector);

let authToken = sessionStorage.getItem('smartstock_token');
let currentUser = JSON.parse(sessionStorage.getItem('smartstock_user') || 'null');
let chatHistory = [];
let uploadMode = 'replace';
let forecastChartInstance = null;
let trendChartInstance = null;
let isDemoLoading = false;

// Color Palette
const COLORS = {
  pine: '#123D31',
  green: '#37C77D',
  mint: '#DFF5E6',
  orange: '#F9B64E',
  line: '#dfe2da',
  muted: '#74807a'
};

if (window.Chart) {
  Chart.defaults.font.family = 'Manrope, Arial, sans-serif';
  Chart.defaults.color = COLORS.muted;
}

async function request(url, options = {}) {
  if (authToken) {
    options.headers = { ...(options.headers || {}), 'X-Workspace-Token': authToken };
  }
  const response = await fetch(url, options);
  if (response.status === 401 && authToken) {
    logout();
    throw new Error('Session expired. Please log in again.');
  }
  if (options.rawResponse) return response;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || 'Something went wrong.');
  return body;
}

function format(value) {
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(value || 0);
}

function escapeHtml(value) {
  const div = document.createElement('div'); div.textContent = value; return div.innerHTML;
}

// Auth Functions
function updateAuthUI() {
  if (currentUser) {
    $('#headerOrg').textContent = currentUser.organization_name || currentUser.organization_id || 'Workspace';
    $('#authBtn').textContent = 'Logout';
    $('#overview-title').textContent = `Good morning, ${currentUser.name.split(' ')[0]}.`;
    const initials = currentUser.name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
    $('#avatar').textContent = initials;
  } else {
    $('#headerOrg').textContent = '';
    $('#authBtn').textContent = 'Login';
    $('#overview-title').textContent = 'Good morning, Kanav.';
    $('#avatar').textContent = 'KS';
  }
}

async function login(email, password) {
  try {
    const data = await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    authToken = data.token;
    currentUser = data.user;
    sessionStorage.setItem('smartstock_token', authToken);
    sessionStorage.setItem('smartstock_user', JSON.stringify(currentUser));
    updateAuthUI();
    hideAuth();
    refresh();
  } catch (err) {
    $('#authError').textContent = err.message;
  }
}

async function register(orgName, name, email, password) {
  try {
    const data = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organization_name: orgName, name, email, password })
    });
    authToken = data.token;
    currentUser = data.user;
    sessionStorage.setItem('smartstock_token', authToken);
    sessionStorage.setItem('smartstock_user', JSON.stringify(currentUser));
    updateAuthUI();
    hideAuth();
    refresh();
  } catch (err) {
    $('#authError').textContent = err.message;
  }
}

function logout() {
  authToken = null;
  currentUser = null;
  sessionStorage.removeItem('smartstock_token');
  sessionStorage.removeItem('smartstock_user');
  updateAuthUI();
  refresh();
}

function showAuth() {
  $('#auth').classList.add('visible');
  $('#authError').textContent = '';
}

function hideAuth() {
  $('#auth').classList.remove('visible');
}

// Charting Functions
function renderCharts(products, summary) {
  const canvas1 = $('#forecastCanvas');
  const canvas2 = $('#trendCanvas');
  
  if (forecastChartInstance) forecastChartInstance.destroy();
  if (trendChartInstance) trendChartInstance.destroy();

  if (!products || products.length === 0 || !window.Chart) {
    return;
  }

  const topProducts = products.slice(0, 6);
  const labels = topProducts.map(p => p.product.length > 13 ? p.product.substring(0, 13) + '...' : p.product);
  const data14d = topProducts.map(p => p.forecast_14d);
  
  forecastChartInstance = new Chart(canvas1, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: '14-Day Forecast',
        data: data14d,
        backgroundColor: COLORS.green,
        borderRadius: 4,
        barPercentage: 0.6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, grid: { color: COLORS.line } },
        x: { grid: { display: false } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  const days = Array.from({length: 30}, (_, i) => `Day ${i+1}`);
  const histData = Array.from({length: 16}, () => Math.floor(Math.random() * 50) + 10);
  const projData = Array(15).fill(null).concat([histData[15]], Array.from({length: 14}, () => Math.floor(Math.random() * 50) + 10));

  trendChartInstance = new Chart(canvas2, {
    type: 'line',
    data: {
      labels: days,
      datasets: [
        {
          label: 'Historical Sales',
          data: histData,
          borderColor: COLORS.pine,
          tension: 0.3,
          pointRadius: 0
        },
        {
          label: 'Forecast Projection',
          data: projData,
          borderColor: COLORS.green,
          borderDash: [5, 5],
          tension: 0.3,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, grid: { color: COLORS.line } },
        x: { grid: { display: false } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

function emptyOverview() {
  ['totalSales', 'totalProducts', 'reorderAlerts', 'documentCount'].forEach((id) => { $(`#${id}`).textContent = '—'; });
  $('#reorderList').className = 'empty-state'; $('#reorderList').textContent = 'No inventory signals yet.';
  $('#productTable').innerHTML = '<tr><td colspan="5" class="muted-cell">Waiting for sales data.</td></tr>';
  $('#anomalyList').className = 'empty-state'; $('#anomalyList').textContent = 'No data to analyse yet.';
  $('#dataReady').textContent = 'No data loaded';
  $('#status').classList.remove('live'); $('#status').lastChild.textContent = ' Workspace empty';
  const banner = $('#demoBanner');
  if (banner) banner.style.display = 'none';
  const demoBtn = $('#dashboardDemo');
  if (demoBtn) {
    demoBtn.innerHTML = 'Load demo workspace <span>→</span>';
    demoBtn.onclick = loadDemo;
  }
  if (forecastChartInstance) forecastChartInstance.destroy();
  if (trendChartInstance) trendChartInstance.destroy();
}

function renderOverview(data) {
  if (!data.ready) { emptyOverview(); return; }
  const { summary, products, anomalies } = data;
  const isDemo = data.is_demo && !authToken;
  $('#totalSales').textContent = format(summary.total_sales);
  $('#totalProducts').textContent = summary.products;
  $('#reorderAlerts').textContent = summary.reorder_alerts;
  $('#documentCount').textContent = summary.documents;
  $('#status').classList.add('live'); 
  $('#status').lastChild.textContent = authToken ? ' Workspace ready' : (isDemo ? ' Demo workspace' : ' Workspace ready');
  $('#dataReady').textContent = `${summary.products} products · ${summary.documents} sources`;

  // Toggle the dashboard header button between "Load demo" and "Reset to my data"
  const demoBtn = $('#dashboardDemo');
  if (demoBtn) {
    if (isDemo) {
      demoBtn.textContent = '';
      demoBtn.innerHTML = 'Reset to my data <span>↗</span>';
      demoBtn.onclick = clearDemo;
    } else {
      demoBtn.innerHTML = 'Load demo workspace <span>→</span>';
      demoBtn.onclick = loadDemo;
    }
  }

  // Show demo banner for first-time visitors
  const banner = $('#demoBanner');
  if (banner) {
    banner.style.display = isDemo ? 'flex' : 'none';
  }
  
  renderCharts(products, summary);
  
  const queue = products.filter((p) => p.reorder);
  $('#reorderList').className = '';
  $('.alert-dot').classList.toggle('active', queue.length > 0);
  $('#reorderList').innerHTML = queue.length ? queue.map((p) => `<div class="reorder-item"><div><strong>${escapeHtml(p.product)}</strong><small>${format(p.latest_inventory)} units on hand · ${p.days_cover || 0} days cover</small></div><span class="signal">REORDER</span></div>`).join('') : '<div class="empty-state" style="height:150px">All products have enough stock for their near-term forecast.</div>';
  
  $('#productTable').innerHTML = products.map((p) => `<tr><td><strong>${escapeHtml(p.product)}</strong></td><td>${format(p.daily_average)}</td><td>${format(p.forecast_14d)}</td><td>${p.latest_inventory === null ? '—' : format(p.latest_inventory)}</td><td><span class="pill ${p.reorder ? 'warning' : ''}">${p.reorder ? 'REORDER' : 'ON TRACK'}</span></td></tr>`).join('');
  
  $('#anomalyList').className = '';
  $('#anomalyList').innerHTML = anomalies.length ? anomalies.slice(0, 4).map((a) => `<div class="anomaly-item"><div><strong>${escapeHtml(a.product)}</strong><small>${a.date} · ${format(a.sales)} units sold</small></div><span class="signal">${a.z_score}σ</span></div>`).join('') : '<div class="empty-state" style="height:85px">No unusual demand patterns detected.</div>';
}

async function refresh() {
  try { 
    const data = await request(`/api/overview`);
    renderOverview(data); 
  } catch (error) { 
    console.error(error); 
  }
}

async function loadDemo() {
  if (isDemoLoading) return;
  isDemoLoading = true;
  const buttons = [$('#demoButton'), $('#dashboardDemo')];
  buttons.forEach((button) => { if(button) button.disabled = true; });
  try {
    await request('/api/demo/reset', { method: 'POST' });
    if(authToken) logout(); // Demo works without auth
    await refresh();
    $('#salesMessage').textContent = 'Demo sales data loaded successfully.'; $('#salesMessage').className = 'upload-message success';
    $('#documentMessage').textContent = '3 demo knowledge documents indexed.'; $('#documentMessage').className = 'upload-message success';
    showView('dashboard');
  } catch (error) { alert(error.message); }
  buttons.forEach((button) => { if(button) button.disabled = false; });
  isDemoLoading = false;
}

async function clearDemo() {
  try {
    await request('/api/demo/clear', { method: 'POST' });
    await refresh();
    showView('data');
  } catch (error) { alert(error.message); }
}

async function upload(input, url, messageNode) {
  const file = input.files[0]; if (!file) return;
  messageNode.textContent = `Processing ${file.name}…`; messageNode.className = 'upload-message';
  const form = new FormData(); 
  form.append('file', file);
  if (url === '/api/sales') form.append('mode', uploadMode);
  try {
    const result = await request(url, { method: 'POST', body: form });
    messageNode.textContent = result.message + (result.rows ? `: ${result.rows} records.` : (result.chunks ? `: ${result.chunks} chunks.` : ''));
    messageNode.className = 'upload-message success'; 
    await refresh();
  } catch (error) { 
    messageNode.textContent = error.message; 
    messageNode.className = 'upload-message error'; 
  }
  input.value = '';
}

async function downloadPDF() {
  const btn = $('#downloadPdfBtn');
  btn.disabled = true;
  try {
    const response = await request('/api/reports/executive', { rawResponse: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'smartstock-executive-briefing.pdf'; a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert('Could not download PDF: ' + err.message);
  }
  btn.disabled = false;
}

async function exportCSV() {
  const btn = $('#exportCsvBtn');
  btn.disabled = true;
  try {
    const response = await request('/api/export/sales', { rawResponse: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'smartstock-sales-export.csv'; a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert('Could not export CSV: ' + err.message);
  }
  btn.disabled = false;
}

// Multi-turn chat
function renderChat() {
  const container = $('#chatAnswer');
  if (chatHistory.length === 0) {
    container.innerHTML = `
      <div class="welcome-orb" style="margin:0 auto">✦</div>
      <h2 style="text-align:center">What can I help you find?</h2>
      <p style="text-align:center;margin:0 auto">Ask about supplier lead times, stock cover, forecasts, replenishment policy, or a specific product.</p>
    `;
    return;
  }
  
  container.innerHTML = chatHistory.map((msg, index) => {
    if (msg.role === 'user') {
      return `<div class="chat-bubble user">${escapeHtml(msg.content)}</div>`;
    } else {
      let sourcesHtml = '';
      if (msg.sources && msg.sources.length) {
        sourcesHtml = `<ul class="source-list">${msg.sources.map(s => `<li>↗ ${escapeHtml(s.name)} · chunk ${s.chunk}</li>`).join('')}</ul>`;
      }
      
      let followUpsHtml = '';
      if (msg.follow_ups && msg.follow_ups.length) {
        followUpsHtml = `<div class="follow-up-chips">
          ${msg.follow_ups.map(f => `<button onclick="ask('${escapeHtml(f.replace(/'/g, "\\'"))}')">${escapeHtml(f)} <span>→</span></button>`).join('')}
        </div>`;
      }
      
      const isLast = index === chatHistory.length - 1;
      let feedbackHtml = '';
      if (isLast && !msg.loading && !msg.error) {
        feedbackHtml = `
          <div class="chat-feedback" data-index="${index}">
            <button class="upvote" onclick="sendFeedback('${escapeHtml(msg.question.replace(/'/g, "\\'"))}', 1)">👍</button>
            <button class="downvote" onclick="sendFeedback('${escapeHtml(msg.question.replace(/'/g, "\\'"))}', -1)">👎</button>
          </div>
        `;
      }
      // Render markdown if marked is available, else raw HTML
      const htmlContent = window.marked ? marked.parse(msg.content) : `<p>${escapeHtml(msg.content)}</p>`;
      return `<div class="chat-bubble assistant">${htmlContent}${sourcesHtml}${followUpsHtml}${feedbackHtml}</div>`;
    }
  }).join('');
  
  container.scrollTop = container.scrollHeight;
}

window.sendFeedback = async function(question, rating) {
  try {
    await request('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, rating: parseInt(rating, 10), comment: '' }),
    });
    const buttons = document.querySelectorAll('.chat-feedback button');
    buttons.forEach(btn => btn.classList.add('voted'));
  } catch (e) {
    console.error('Feedback error', e);
  }
}

async function ask(questionText) {
  const field = $('#question'); 
  const value = (questionText || field.value).trim(); 
  if (!value) return;
  
  // Create history payload without the new question
  const historyPayload = chatHistory
    .filter(msg => !msg.loading && !msg.error)
    .map(msg => ({ role: msg.role, content: msg.content }));
  
  chatHistory.push({ role: 'user', content: value });
  chatHistory.push({ role: 'assistant', content: 'Finding supporting sources…', loading: true });
  renderChat();
  $('#askButton').disabled = true;
  field.value = '';
  
  try {
    const result = await request('/api/chat', { 
      method: 'POST', 
      headers: { 'Content-Type': 'application/json' }, 
      body: JSON.stringify({ question: value, history: historyPayload }) 
    });
    
    chatHistory[chatHistory.length - 1] = {
      role: 'assistant',
      content: result.answer,
      sources: result.sources || [],
      follow_ups: result.follow_ups || [],
      question: value
    };
  } catch (error) { 
    chatHistory[chatHistory.length - 1] = {
      role: 'assistant',
      content: `Could not answer: ${error.message}`,
      error: true
    };
  }
  
  renderChat();
  $('#askButton').disabled = false;
}

window.clearChat = function() {
  chatHistory = [];
  renderChat();
}

// Intelligence
function renderAccuracy(accuracy) {
  const container = $('#accuracyContent');
  if (!accuracy || !accuracy.length) {
    container.innerHTML = '<div class="empty-state" style="height:150px">No accuracy data available.</div>';
    return;
  }
  let html = '';
  accuracy.forEach(model => {
    html += `
      <div class="model-row">
        <div class="model-name">${escapeHtml(model.name)} ${model.selected ? '<span class="selected-badge">Active</span>' : ''}</div>
        <div class="model-metrics">
          <span>MAE: <strong>${format(model.mae)}</strong></span>
          <span>RMSE: <strong>${format(model.rmse)}</strong></span>
        </div>
      </div>
    `;
  });
  html += '<div class="accuracy-note">Accuracy measured over a 30-day holdout set. The model with the lowest Mean Absolute Error (MAE) is automatically selected.</div>';
  container.innerHTML = html;
}

function renderRecommendations(recommendations) {
  const table = $('#recommendationsTable');
  if (!recommendations || !recommendations.length) {
    table.innerHTML = '<tr><td colspan="7" class="muted-cell">No order recommendations.</td></tr>';
    return;
  }
  table.innerHTML = recommendations.map(r => {
    let priorityClass = 'on-track';
    if (r.priority === 'Critical') priorityClass = 'critical';
    if (r.priority === 'High') priorityClass = 'high';
    const reasonsHtml = (r.reasons && r.reasons.length) 
        ? `<ul class="reason-list">${r.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`
        : '<span class="muted-cell">No additional details.</span>';
    
    return `
      <tr>
        <td><strong>${escapeHtml(r.product)}</strong></td>
        <td><span class="priority-badge ${priorityClass}">${r.priority}</span></td>
        <td>${format(r.inventory)}</td>
        <td>${format(r.safety_stock)}</td>
        <td>${format(r.target_stock)}</td>
        <td><strong>${format(r.recommended_order)}</strong></td>
        <td><button class="btn-secondary" onclick="this.closest('tr').nextElementSibling.classList.toggle('row-expanded')" style="padding:4px 8px;font-size:9px">Signals <span>↓</span></button></td>
      </tr>
      <tr class="reason-row">
        <td colspan="7">
          <div class="reason-content">
            <p class="eyebrow">REORDER SIGNALS & REASONING</p>
            ${reasonsHtml}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function renderAlerts(alerts) {
  const container = $('#alertsContent');
  if (!alerts || !alerts.length) {
    container.innerHTML = '<div class="empty-state" style="height:150px">No operational alerts.</div>';
    return;
  }
  container.innerHTML = alerts.map(a => {
    const iconClass = a.type === 'stock' ? 'stock' : 'demand';
    const iconChar = a.type === 'stock' ? '!' : '↑';
    return `
      <div class="alert-item">
        <div class="alert-icon ${iconClass}">${iconChar}</div>
        <div>
          <strong>${escapeHtml(a.title)}</strong>
          <div class="alert-detail">${escapeHtml(a.detail)}</div>
        </div>
      </div>
    `;
  }).join('');
}

async function loadIntelligence() {
  try {
    const data = await request('/api/intelligence');
    if (!data || !data.ready) { 
      $('#accuracyContent').innerHTML = '<div class="empty-state" style="height:150px">Waiting for data...</div>';
      $('#alertsContent').innerHTML = '<div class="empty-state" style="height:150px">Waiting for data...</div>';
      $('#recommendationsTable').innerHTML = '<tr><td colspan="7" class="muted-cell">Waiting for intelligence data.</td></tr>';
      return; 
    }
    renderAccuracy(data.accuracy);
    renderRecommendations(data.recommendations);
    renderAlerts(data.alerts);
  } catch (err) {
    console.error('Failed to load intelligence', err);
  }
}

// Router & Events
function showView(view, shouldUpdateHash = true) {
  const target = document.getElementById(view);
  if (!target) return;
  document.querySelectorAll('.view').forEach((item) => item.classList.toggle('active-view', item === target));
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  if (shouldUpdateHash && window.location.hash !== `#${view}`) window.location.hash = view;
  window.scrollTo({ top: 0, behavior: 'smooth' });
  
  if (view === 'intelligence') {
    loadIntelligence();
  }
}

// Event Listeners
document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
window.addEventListener('hashchange', () => showView(window.location.hash.slice(1), false));

$('#demoButton').addEventListener('click', loadDemo); 
if ($('#dashboardDemo')) $('#dashboardDemo').addEventListener('click', loadDemo);
$('#salesFile').addEventListener('change', (event) => upload(event.target, '/api/sales', $('#salesMessage')));
$('#documentFile').addEventListener('change', (event) => upload(event.target, '/api/documents', $('#documentMessage')));

$('#askButton').addEventListener('click', () => ask());
$('#question').addEventListener('keydown', (event) => { if (event.key === 'Enter') ask(); });
document.querySelectorAll('.suggestions button').forEach((button) => button.addEventListener('click', () => ask(button.dataset.question)));

$('#downloadPdfBtn').addEventListener('click', downloadPDF);
$('#exportCsvBtn').addEventListener('click', exportCSV);

// Upload mode toggles
document.querySelectorAll('.mode-toggle button').forEach(btn => {
  btn.addEventListener('click', (e) => {
    document.querySelectorAll('.mode-toggle button').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    uploadMode = e.target.dataset.mode;
  });
});

// Auth form toggles
$('#tabLogin').addEventListener('click', () => {
  $('#tabLogin').classList.add('active');
  $('#tabRegister').classList.remove('active');
  $('#authOrgName').style.display = 'none';
  $('#authName').style.display = 'none';
  $('#authSubmit').textContent = 'Login';
  $('#authError').textContent = '';
});

$('#tabRegister').addEventListener('click', () => {
  $('#tabRegister').classList.add('active');
  $('#tabLogin').classList.remove('active');
  $('#authOrgName').style.display = 'block';
  $('#authName').style.display = 'block';
  $('#authSubmit').textContent = 'Register';
  $('#authError').textContent = '';
});

$('#authForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const email = $('#authEmail').value;
  const password = $('#authPassword').value;
  if ($('#tabLogin').classList.contains('active')) {
    login(email, password);
  } else {
    const orgName = $('#authOrgName').value;
    const name = $('#authName').value;
    register(orgName, name, email, password);
  }
});

$('#demoLink').addEventListener('click', hideAuth);

$('#authBtn').addEventListener('click', () => {
  if (authToken) {
    logout();
  } else {
    showAuth();
  }
});

// Init
async function init() {
  if (authToken) {
    try {
      const data = await request('/api/auth/me');
      currentUser = data;
      sessionStorage.setItem('smartstock_user', JSON.stringify(data));
    } catch (e) {
      logout();
    }
  }
  updateAuthUI();
  showView(window.location.hash.slice(1) || 'dashboard', false);
  refresh();
}

init();
