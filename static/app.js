const $ = (selector) => document.querySelector(selector);

let authToken = sessionStorage.getItem('smartstock_token');
let currentUser = JSON.parse(sessionStorage.getItem('smartstock_user') || 'null');
let chatHistory = [];
let chatRequestId = 0;
let chatPending = false;
let copiedAnswerIndex = -1;
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

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
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

function applyTheme(theme) {
  const isDark = theme === 'dark';
  document.body.classList.toggle('dark-theme', isDark);
  const toggle = $('#themeToggle');
  if (toggle) toggle.textContent = isDark ? '☀️' : '🌙';
  localStorage.setItem('smartstock_theme', theme);
  updateChartTheme();
}

function getChartTheme() {
  const isDark = document.body.classList.contains('dark-theme');
  return {
    text: isDark ? '#a7aca5' : COLORS.muted,
    grid: isDark ? '#3b433d' : COLORS.line,
    historical: isDark ? '#d0b579' : COLORS.pine,
    projection: isDark ? '#a4c88b' : COLORS.green
  };
}

function updateChartTheme() {
  if (!window.Chart) return;
  const theme = getChartTheme();
  Chart.defaults.color = theme.text;
  [forecastChartInstance, trendChartInstance].forEach((chart) => {
    if (!chart) return;
    chart.options.scales.y.grid.color = theme.grid;
    chart.options.scales.y.ticks.color = theme.text;
    chart.options.scales.x.ticks.color = theme.text;
    if (chart === trendChartInstance) {
      chart.data.datasets[0].borderColor = theme.historical;
      chart.data.datasets[1].borderColor = theme.projection;
    }
    chart.update('none');
  });
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
  const chartTheme = getChartTheme();
  const labels = topProducts.map(p => p.product.length > 13 ? p.product.substring(0, 13) + '...' : p.product);
  const data14d = topProducts.map(p => p.forecast_14d);
  
  forecastChartInstance = new Chart(canvas1, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: '14-Day Forecast',
        data: data14d,
        backgroundColor: chartTheme.projection,
        borderRadius: 4,
        barPercentage: 0.6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, grid: { color: chartTheme.grid }, ticks: { color: chartTheme.text } },
        x: { grid: { display: false }, ticks: { color: chartTheme.text } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  let days = [];
  let histData = [];
  let projData = [];

  if (products && products.length > 0) {
    const p0 = products[0];
    const numHist = p0.history.length;
    const numFcast = p0.forecast.length;
    
    days = p0.history.map(h => h.date).concat(Array.from({length: numFcast}, (_, i) => `+${i+1}d`));
    histData = new Array(numHist).fill(0);
    projData = new Array(numHist + numFcast).fill(null);
    
    products.forEach(p => {
      p.history.forEach((h, i) => { histData[i] += h.sales; });
      p.forecast.forEach((f, i) => {
        projData[numHist + i] = (projData[numHist + i] || 0) + f;
      });
    });
    // Connect the projection line
    projData[numHist - 1] = histData[numHist - 1];
  }

  trendChartInstance = new Chart(canvas2, {
    type: 'line',
    data: {
      labels: days,
      datasets: [
        {
          label: 'Historical Sales',
          data: histData,
          borderColor: chartTheme.historical,
          tension: 0.3,
          pointRadius: 0
        },
        {
          label: 'Forecast Projection',
          data: projData,
          borderColor: chartTheme.projection,
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
        y: { beginAtZero: true, grid: { color: chartTheme.grid }, ticks: { color: chartTheme.text } },
        x: { grid: { display: false }, ticks: { color: chartTheme.text } }
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
  
  $('#productTable').innerHTML = products.map((p) => `<tr><td><strong>${escapeHtml(p.product)}</strong></td><td>${format(p.daily_average)}</td><td>${format(p.forecast_14d)}</td><td>${p.latest_inventory === null ? '—' : format(p.latest_inventory)}</td><td><span class="pill model-pill">${escapeHtml(p.forecast_model || 'Auto')}</span><small class="model-mae">MAE ${format(p.model_mae || 0)}</small></td><td><span class="pill ${p.reorder ? 'warning' : ''}">${p.reorder ? 'REORDER' : 'ON TRACK'}</span></td></tr>`).join('');
  
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

async function previewSalesCsv(file) {
  const previewNode = $('#salesPreview');
  if (!file) {
    previewNode.style.display = 'none';
    previewNode.innerHTML = '';
    return null;
  }

  const form = new FormData();
  form.append('file', file);
  const preview = await request('/api/sales/preview', { method: 'POST', body: form });
  const mapping = preview.mapping || [];
  const expected = { Date: 'date', Product: 'product', 'Quantity/Sales': 'quantity', Inventory: 'inventory' };
  const autoMatches = mapping.filter(item => item.header.toLowerCase().replace(/[_-]/g, ' ').trim() !== expected[item.field]);
  const mappingHtml = mapping.map(item => `<span class="csv-map-chip">${escapeHtml(item.header)} <b>→</b> ${escapeHtml(item.field)}</span>`).join('');
  const previewRows = (preview.preview || []).map(row => `
    <tr><td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.product)}</td><td>${escapeHtml(String(row.quantity))}</td><td>${escapeHtml(row.inventory == null ? '—' : String(row.inventory))}</td></tr>
  `).join('');
  previewNode.innerHTML = `
    <div class="csv-preview-summary"><strong>${format(preview.rows)} valid rows</strong><span>Dates, headers, and formatted numbers are normalized automatically.</span></div>
    <div class="csv-mapping-list">${mappingHtml}</div>
    <div class="table-scroll"><table class="csv-preview-table"><thead><tr><th>DATE</th><th>PRODUCT</th><th>QUANTITY</th><th>INVENTORY</th></tr></thead><tbody>${previewRows}</tbody></table></div>
  `;
  previewNode.style.display = 'block';
  return { ...preview, autoMatches };
}

async function upload(input, url, messageNode) {
  const file = input.files[0]; if (!file) return;
  input.disabled = true;
  messageNode.textContent = `Checking ${file.name} and matching its columns…`;
  messageNode.className = 'upload-message';
  try {
    let preview = null;
    if (url === '/api/sales') preview = await previewSalesCsv(file);
    const form = new FormData();
    form.append('file', file);
    if (url === '/api/sales') form.append('mode', uploadMode);
    messageNode.textContent = url === '/api/sales' ? `Validated ${format(preview.rows)} rows. Uploading…` : `Reading ${file.name}…`;
    const result = await request(url, { method: 'POST', body: form });
    const autoMatchNote = preview?.autoMatches.length
      ? ` Auto-matched: ${preview.autoMatches.map(item => `${item.header} → ${item.field}`).join(', ')}.`
      : '';
    messageNode.textContent = result.message + (result.rows ? `: ${format(result.rows)} records.` : (result.chunks ? `: ${result.chunks} chunks.` : '')) + autoMatchNote;
    messageNode.className = 'upload-message success'; 
    await refresh();
  } catch (error) {
    messageNode.textContent = error.message;
    messageNode.className = 'upload-message error';
    const previewNode = $('#salesPreview');
    if (url === '/api/sales') {
      previewNode.innerHTML = `<div class="csv-preview-error"><strong>We couldn’t safely prepare this file.</strong><span>${escapeHtml(error.message)}</span><small>Fix the indicated row or adjust its header, then choose the file again. Your existing data was not changed.</small></div>`;
      previewNode.style.display = 'block';
    }
  } finally {
    input.disabled = false;
    input.value = '';
  }
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
function getChatGreeting() {
  const hour = new Date().getHours();
  const salutation = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const firstName = currentUser?.name?.trim().split(/\s+/)[0];
  return firstName ? `${salutation}, ${firstName}.` : `${salutation}.`;
}

function getGreetingReply() {
  const firstName = currentUser?.name?.trim().split(/\s+/)[0];
  return firstName
    ? `Hi, ${firstName}! What would you like to look into today?`
    : 'Hi! What would you like to look into today?';
}

function isStandaloneGreeting(value) {
  return /^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))(?:[,.!\s]+(?:there|smartstock|assistant|team|everyone))?[,.!\s]*$/i.test(value);
}

function getDefaultFollowUps(question, answer) {
  const query = question.toLowerCase();
  const response = answer.toLowerCase();
  if (/lead time|supplier|minimum order|\bmoq\b/.test(query)) {
    return ['Which product has the longest supplier lead time?', 'What are the minimum order quantities for each product?'];
  }
  if (/forecast|demand|sales|promotion/.test(query)) {
    return ['Which products need reordering right now?', 'How does forecast demand compare with stock on hand?'];
  }
  if (/reorder|stock|inventory|on hand/.test(query)) {
    return ['Which products need attention first?', 'How much safety stock should we hold?'];
  }
  if (/could not find supporting|no supporting information/.test(response)) return [];
  return ['What should I look at next in my inventory?', 'Can you summarize the relevant policy?'];
}

function renderChat() {
  const container = $('#chatAnswer');
  if (chatHistory.length === 0) {
    container.innerHTML = `
      <div class="welcome-orb" style="margin:0 auto">✦</div>
      <h2 class="welcome-title">${escapeHtml(getChatGreeting())}</h2>
      <p class="welcome-copy">What would you like to explore? I can help with supplier policies, stock risks, and product forecasts.</p>
      <div class="welcome-prompts" aria-label="Suggested questions">
        <button data-chat-action="ask" data-question="Which products need reordering right now?">Check reorder risks <span>→</span></button>
        <button data-chat-action="ask" data-question="What are the supplier lead times and minimum order quantities?">Supplier lead times <span>→</span></button>
        <button data-chat-action="ask" data-question="What is the 14-day forecast for Mechanical Keyboard?">Explore a forecast <span>→</span></button>
      </div>
    `;
    requestAnimationFrame(() => { container.scrollTop = 0; });
    return;
  }
  
  container.innerHTML = chatHistory.map((msg, index) => {
    if (msg.role === 'user') {
      return `<div class="chat-bubble user">${escapeHtml(msg.content)}</div>`;
    } else {
      let sourcesHtml = '';
      if (msg.sources && msg.sources.length) {
        sourcesHtml = `<details class="source-details"><summary>${msg.sources.length} supporting ${msg.sources.length === 1 ? 'source' : 'sources'}</summary><ul class="source-list">${msg.sources.map(s => `<li><strong>${escapeHtml(s.name)}</strong><small>Chunk ${escapeHtml(String(s.chunk))}</small>${s.excerpt ? `<p>${escapeHtml(s.excerpt)}</p>` : ''}</li>`).join('')}</ul></details>`;
      }
      
      let followUpsHtml = '';
      if (msg.follow_ups && msg.follow_ups.length) {
        followUpsHtml = `<div class="follow-up-chips">
          ${msg.follow_ups.map(f => `<button data-chat-action="ask" data-question="${escapeAttribute(f)}">${escapeHtml(f)} <span>→</span></button>`).join('')}
        </div>`;
      }
      
      const isLast = index === chatHistory.length - 1;
      let feedbackHtml = '';
      if (!msg.loading && !msg.error && !msg.conversationOnly) {
        const copied = copiedAnswerIndex === index;
        const rating = msg.rating;
        feedbackHtml = `
          <div class="chat-feedback" data-index="${index}">
            <button data-chat-action="copy" data-index="${index}" aria-label="Copy answer">${copied ? 'Copied' : msg.copyError ? 'Copy unavailable' : 'Copy'}</button>
            <button class="upvote${rating === 1 ? ' voted' : ''}" data-chat-action="feedback" data-index="${index}" data-rating="1" aria-label="Helpful answer" ${rating ? 'disabled' : ''}>👍</button>
            <button class="downvote${rating === -1 ? ' voted' : ''}" data-chat-action="feedback" data-index="${index}" data-rating="-1" aria-label="Not helpful" ${rating ? 'disabled' : ''}>👎</button>
          </div>
        `;
      }
      if (msg.loading) {
        return `<div class="chat-bubble assistant chat-loading" role="status"><span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>${escapeHtml(msg.content)}</div>`;
      }
      const htmlContent = window.marked ? marked.parse(msg.content) : `<p>${escapeHtml(msg.content)}</p>`;
      const retryHtml = msg.error && msg.question ? `<button class="chat-retry" data-chat-action="retry" data-index="${index}">Try again</button>` : '';
      const noSourceAction = !msg.conversationOnly && msg.sources && msg.sources.length === 0
        ? '<button class="chat-retry" data-chat-action="view" data-view="data">Review workspace sources</button>'
        : '';
      return `<div class="chat-bubble assistant${msg.error ? ' chat-error' : ''}">${htmlContent}${retryHtml}${noSourceAction}${sourcesHtml}${followUpsHtml}${feedbackHtml}</div>`;
    }
  }).join('');
  
  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

async function sendFeedback(index, rating) {
  const msg = chatHistory[index];
  if (!msg || !msg.question || msg.rating) return;
  try {
    await request('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: msg.question, rating: parseInt(rating, 10), comment: '' }),
    });
    msg.rating = parseInt(rating, 10);
    renderChat();
  } catch (e) {
    console.error('Feedback error', e);
  }
}

async function ask(questionText) {
  const field = $('#question'); 
  const value = (questionText || field.value).trim(); 
  if (!value || chatPending) return;
  if (isStandaloneGreeting(value)) {
    chatHistory.push({ role: 'user', content: value });
    chatHistory.push({
      role: 'assistant',
      content: getGreetingReply(),
      conversationOnly: true,
      follow_ups: ['Which products need reordering right now?', 'What are the supplier lead times and minimum order quantities?']
    });
    field.value = '';
    renderChat();
    return;
  }
  const requestId = ++chatRequestId;
  chatPending = true;
  
  // Create history payload without the new question
  const historyPayload = chatHistory
    .filter(msg => !msg.loading && !msg.error)
    .map(msg => ({ role: msg.role, content: msg.content }));
  
  chatHistory.push({ role: 'user', content: value });
  chatHistory.push({ role: 'assistant', content: 'Searching your workspace and checking supporting sources…', loading: true, requestId, question: value });
  renderChat();
  $('#askButton').disabled = true;
  $('#askButton').setAttribute('aria-label', 'Finding an answer');
  field.value = '';
  
  try {
    const result = await request('/api/chat', { 
      method: 'POST', 
      headers: { 'Content-Type': 'application/json' }, 
      body: JSON.stringify({ question: value, history: historyPayload }) 
    });
    
    const loadingIndex = chatHistory.findIndex(msg => msg.requestId === requestId);
    if (requestId !== chatRequestId || loadingIndex < 0) return;
    chatHistory[loadingIndex] = {
      role: 'assistant',
      content: result.answer,
      sources: result.sources || [],
      follow_ups: result.sources && result.sources.length
        ? Array.isArray(result.follow_ups) && result.follow_ups.length
          ? result.follow_ups.filter(item => typeof item === 'string').slice(0, 2)
          : getDefaultFollowUps(value, result.answer)
        : [],
      question: value
    };
  } catch (error) { 
    const loadingIndex = chatHistory.findIndex(msg => msg.requestId === requestId);
    if (requestId !== chatRequestId || loadingIndex < 0) return;
    chatHistory[loadingIndex] = {
      role: 'assistant',
      content: `Could not answer: ${error.message}`,
      error: true,
      question: value
    };
  }
  
  chatPending = false;
  renderChat();
  $('#askButton').disabled = false;
  $('#askButton').setAttribute('aria-label', 'Ask question');
}

window.clearChat = function() {
  chatRequestId += 1;
  chatPending = false;
  copiedAnswerIndex = -1;
  $('#askButton').disabled = false;
  $('#askButton').setAttribute('aria-label', 'Ask question');
  chatHistory = [];
  renderChat();
}

async function copyAnswer(index) {
  const msg = chatHistory[index];
  if (!msg || msg.role !== 'assistant' || msg.loading || msg.error) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(msg.content);
    } else {
      const temporaryInput = document.createElement('textarea');
      temporaryInput.value = msg.content;
      temporaryInput.setAttribute('readonly', '');
      temporaryInput.style.position = 'fixed';
      temporaryInput.style.opacity = '0';
      document.body.appendChild(temporaryInput);
      temporaryInput.select();
      const copied = document.execCommand('copy');
      temporaryInput.remove();
      if (!copied) throw new Error('Clipboard access is unavailable');
    }
    msg.copyError = false;
    copiedAnswerIndex = index;
    renderChat();
    setTimeout(() => {
      if (copiedAnswerIndex === index) {
        copiedAnswerIndex = -1;
        renderChat();
      }
    }, 1400);
  } catch (error) {
    console.error('Could not copy answer', error);
    msg.copyError = true;
    renderChat();
  }
}

$('#chatAnswer').addEventListener('click', (event) => {
  const button = event.target.closest('[data-chat-action]');
  if (!button) return;
  const { chatAction, question, index, rating } = button.dataset;
  if (chatAction === 'ask') ask(question);
  if (chatAction === 'feedback') sendFeedback(Number(index), Number(rating));
  if (chatAction === 'copy') copyAnswer(Number(index));
  if (chatAction === 'retry') ask(chatHistory[Number(index)]?.question);
  if (chatAction === 'view') showView(button.dataset.view);
});

// Intelligence
function renderAccuracy(accuracy) {
  const container = $('#accuracyContent');
  const models = accuracy && accuracy.models;
  if (!models || !models.length) {
    container.innerHTML = '<div class="empty-state" style="height:150px">No accuracy data available.</div>';
    return;
  }
  let html = '';
  models.forEach(model => {
    html += `
      <div class="model-row">
        <div class="model-name">${escapeHtml(model.name)} ${model.selected ? '<span class="selected-badge">Best</span>' : ''}</div>
        <div class="model-metrics">
          <span>MAE: <strong>${format(model.mae)}</strong></span>
          <span>RMSE: <strong>${format(model.rmse)}</strong></span>
          <span>Samples: <strong>${model.samples}</strong></span>
        </div>
      </div>
    `;
  });
  const note = accuracy.note || 'Accuracy measured via holdout backtesting. The model with the lowest MAE is auto-selected per product.';
  html += `<div class="accuracy-note">${escapeHtml(note)}</div>`;
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

async function loadSuppliers() {
  try {
    const data = await request('/api/suppliers');
    const table = $('#suppliersTable');
    if (!data.suppliers || Object.keys(data.suppliers).length === 0) {
      table.innerHTML = '<tr><td colspan="7" class="muted-cell">No suppliers configured.</td></tr>';
      return;
    }
    let html = '';
    for (const [product, suppliers] of Object.entries(data.suppliers)) {
      suppliers.forEach(s => {
        html += `
          <tr>
            <td><strong>${escapeHtml(s.product)}</strong></td>
            <td>${escapeHtml(s.supplier_name)}</td>
            <td>${s.lead_time_days} days</td>
            <td>±${s.lead_time_variability_days} days</td>
            <td>${s.moq} units</td>
            <td>${s.is_primary ? '<span class="selected-badge">Primary</span>' : ''}</td>
            <td><button class="btn-secondary" onclick="deleteSupplier(${s.id})" style="padding:4px 8px;font-size:9px;color:var(--danger, #d32f2f)">Remove</button></td>
          </tr>
        `;
      });
    }
    table.innerHTML = html;
  } catch (err) {
    console.error('Failed to load suppliers', err);
  }
}

async function deleteSupplier(id) {
  if (!confirm("Remove this supplier?")) return;
  try {
    await request(`/api/suppliers/${id}`, { method: 'DELETE' });
    loadSuppliers();
    loadIntelligence();
  } catch (err) { alert(err.message); }
}

$('#addSupplierForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button');
  btn.disabled = true;
  try {
    await request('/api/suppliers', {
      method: 'POST',
      body: JSON.stringify({
        product: $('#supProduct').value,
        supplier_name: $('#supName').value,
        lead_time_days: parseInt($('#supLead').value),
        lead_time_variability_days: parseInt($('#supVar').value),
        moq: parseInt($('#supMoq').value),
        is_primary: $('#supPrimary').checked
      })
    });
    e.target.reset();
    loadSuppliers();
    loadIntelligence();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

async function loadNotifications() {
  try {
    const data = await request('/api/notifications');
    const container = $('#notificationsContent');
    if (!data.configs || data.configs.length === 0) {
      container.innerHTML = '<div class="empty-state">No notifications configured.</div>';
      return;
    }
    container.innerHTML = data.configs.map(c => `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding-bottom:8px; border-bottom:1px dashed var(--line);">
        <div>
          <strong style="text-transform:uppercase; font-size:10px; color:var(--text-muted);">${c.method}</strong><br>
          <span style="font-size:12px;">${escapeHtml(c.target)}</span><br>
          <span style="font-size:10px; color:var(--text-muted);">
            Triggers: ${c.on_reorder ? 'Reorders' : ''} ${c.on_reorder && c.on_anomaly ? '&' : ''} ${c.on_anomaly ? 'Anomalies' : ''}
          </span>
        </div>
        <button class="btn-secondary" onclick="deleteNotification(${c.id})" style="padding:4px 8px;font-size:9px;color:var(--danger, #d32f2f)">Remove</button>
      </div>
    `).join('');
  } catch (err) {
    console.error('Failed to load notifications', err);
  }
}

async function deleteNotification(id) {
  if (!confirm("Remove this notification config?")) return;
  try {
    await request(`/api/notifications/${id}`, { method: 'DELETE' });
    loadNotifications();
  } catch (err) { alert(err.message); }
}

$('#addNotificationForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button');
  btn.disabled = true;
  try {
    await request('/api/notifications', {
      method: 'POST',
      body: JSON.stringify({
        method: $('#notifMethod').value,
        target: $('#notifTarget').value,
        on_reorder: $('#notifReorder').checked,
        on_anomaly: $('#notifAnomaly').checked
      })
    });
    e.target.reset();
    loadNotifications();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});


document.addEventListener('DOMContentLoaded', () => {
  const savedTheme = localStorage.getItem('smartstock_theme') || 'light';
  applyTheme(savedTheme);
  renderChat();
  const toggle = $('#themeToggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const nextTheme = document.body.classList.contains('dark-theme') ? 'light' : 'dark';
      applyTheme(nextTheme);
    });
  }
});

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
    loadSuppliers();
    loadNotifications();
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
$('#question').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.isComposing) {
    event.preventDefault();
    ask();
  }
});
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
