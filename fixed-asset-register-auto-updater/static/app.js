async function refreshStatus() {
  const res = await fetch('/api/status');
  const st = await res.json();

  const baselineDiv = document.getElementById('baseline-status');
  const updateForm = document.getElementById('update-form');
  const runBtn = document.getElementById('run-btn');
  const tabsDiv = document.getElementById('tabs-summary');
  const closingDiv = document.getElementById('closing-date');
  const historyDiv = document.getElementById('history');

  if (!st.has_baseline) {
    baselineDiv.className = 'status warn';
    baselineDiv.textContent = 'No baseline FAR uploaded yet. Upload your existing FAR workbook below to get started.';
    runBtn.disabled = true;
    tabsDiv.innerHTML = '<p class="empty">No FAR uploaded yet.</p>';
    closingDiv.textContent = '';
  } else {
    baselineDiv.className = 'status ok';
    baselineDiv.textContent = `Baseline FAR loaded - ${st.tabs.length} asset-class tab(s) recognized.`;
    runBtn.disabled = false;
    closingDiv.textContent = st.far_closing_date
      ? `Register's current closing position: ${st.far_closing_date}. The next update only needs to cover transactions after this date.`
      : 'Closing position not yet determined - it will be set from the first period you process.';
    if (st.tabs.length === 0) {
      tabsDiv.innerHTML = '<p class="empty">No recognizable asset-class tabs were found in this workbook.</p>';
    } else {
      tabsDiv.innerHTML = '<table><thead><tr><th>Tab</th><th>Rows</th><th>Depreciation rate used</th></tr></thead><tbody>' +
        st.tabs.map(t => `<tr><td>${escapeHtml(t.name)}</td><td>${t.rows}</td><td>${t.rate !== null && t.rate !== undefined ? (t.rate * 100).toFixed(2) + '%' : '<span class="empty">not determined</span>'}</td></tr>`).join('') +
        '</tbody></table>';
    }
  }

  if (!st.run_history || st.run_history.length === 0) {
    historyDiv.innerHTML = '<p class="empty">No updates have been run yet.</p>';
  } else {
    historyDiv.innerHTML = '<table><thead><tr><th>Run date</th><th>Period</th><th>Additions</th><th>Disposals</th><th>Exceptions</th><th>Duplicates skipped</th></tr></thead><tbody>' +
      st.run_history.slice().reverse().map(h => `<tr>
        <td>${h.run_date}</td>
        <td>${h.period_start} to ${h.period_end}</td>
        <td>${h.additions}</td>
        <td>${h.disposals}</td>
        <td>${h.exceptions}</td>
        <td>${h.skipped_duplicates}</td>
      </tr>`).join('') + '</tbody></table>';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.getElementById('baseline-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById('far-file');
  if (!fileInput.files.length) return;
  const fd = new FormData();
  fd.append('far_file', fileInput.files[0]);
  const resultDiv = document.getElementById('baseline-result');
  resultDiv.textContent = 'Uploading and inspecting baseline FAR…';
  try {
    const res = await fetch('/api/baseline', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) {
      resultDiv.innerHTML = `<p class="status err">${escapeHtml(data.detail || 'Upload failed.')}</p>`;
    } else {
      resultDiv.innerHTML = `<p class="status ok">${escapeHtml(data.message)}</p>`;
      fileInput.value = '';
      await refreshStatus();
    }
  } catch (err) {
    resultDiv.innerHTML = `<p class="status err">${escapeHtml(String(err))}</p>`;
  }
});

document.getElementById('update-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const invoiceFiles = document.getElementById('invoice-files').files;
  const glFile = document.getElementById('gl-file').files[0];
  const statusDiv = document.getElementById('update-status');
  const resultsSection = document.getElementById('results-section');
  const resultsDiv = document.getElementById('results');
  const runBtn = document.getElementById('run-btn');

  if (!invoiceFiles.length || !glFile) {
    statusDiv.className = 'status err';
    statusDiv.textContent = 'Please provide at least one invoice and the GL.';
    return;
  }

  const fd = new FormData();
  fd.append('gl_file', glFile);
  for (const f of invoiceFiles) fd.append('invoices', f);

  runBtn.disabled = true;
  statusDiv.className = 'status';
  statusDiv.textContent = 'Processing invoices and GL - this can take a moment…';
  resultsSection.style.display = 'none';

  try {
    const res = await fetch('/api/run_update', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) {
      statusDiv.className = 'status err';
      statusDiv.textContent = data.detail || 'Update failed.';
      return;
    }
    if (data.status === 'nothing_to_update') {
      statusDiv.className = 'status warn';
      statusDiv.textContent = data.message;
      return;
    }
    statusDiv.className = 'status ok';
    statusDiv.textContent = `Update complete for period ${data.period}.`;
    renderResults(data);
    resultsSection.style.display = 'block';
    await refreshStatus();
  } catch (err) {
    statusDiv.className = 'status err';
    statusDiv.textContent = String(err);
  } finally {
    runBtn.disabled = false;
  }
});

function renderResults(data) {
  const resultsDiv = document.getElementById('results');
  let html = '';
  html += `<div class="summary-counts">
    <div><div class="count">${data.additions.length}</div><div class="label">Assets added</div></div>
    <div><div class="count">${data.disposals.length}</div><div class="label">Disposals processed</div></div>
    <div><div class="count">${data.skipped_duplicates.length}</div><div class="label">Duplicates skipped</div></div>
    <div><div class="count">${data.exceptions.length}</div><div class="label">Exceptions</div></div>
  </div>`;

  if (data.additions.length) {
    html += '<h3>New assets added</h3><table><thead><tr><th>Tab</th><th>Description</th><th>Cost</th><th>Date</th><th>Dep this period</th><th>WDV</th></tr></thead><tbody>';
    for (const a of data.additions) {
      html += `<tr><td>${escapeHtml(a.tab)}</td><td>${escapeHtml(a.description)}</td><td>${fmtNum(a.cost)}</td><td>${a.date}</td><td>${fmtNum(a.dep_this_period)}</td><td>${fmtNum(a.wdv)}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  if (data.disposals.length) {
    html += '<h3>Disposals processed</h3><table><thead><tr><th>Tab</th><th>Row</th><th>Disposal date</th><th>Dep this period</th><th>Closing WDV</th></tr></thead><tbody>';
    for (const d of data.disposals) {
      html += `<tr><td>${escapeHtml(d.tab)}</td><td>${d.row}</td><td>${d.disposal_date}</td><td>${fmtNum(d.dep_this_period)}</td><td>${fmtNum(d.wdv)}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  if (data.exceptions.length) {
    html += '<h3>Exceptions - review required</h3><table><thead><tr><th>Source</th><th>Item</th><th>Reason</th><th>Details</th></tr></thead><tbody>';
    for (const x of data.exceptions) {
      html += `<tr><td><span class="badge">${escapeHtml(x.source)}</span></td><td>${escapeHtml(x.item)}</td><td>${escapeHtml(x.reason)}</td><td>${escapeHtml(x.details)}</td></tr>`;
    }
    html += '</tbody></table><p class="hint">These items were also written to the "Exceptions – Review Required" tab in the downloaded FAR.</p>';
  }

  if (!data.additions.length && !data.disposals.length && !data.exceptions.length) {
    html += '<p class="empty">Nothing new was found to process for this period.</p>';
  }

  html += '<p><a class="button" href="/api/download">Download updated FAR</a></p>';
  resultsDiv.innerHTML = html;
}

function fmtNum(n) {
  if (n === null || n === undefined) return '';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

refreshStatus();
