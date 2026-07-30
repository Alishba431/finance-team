let SESSION_ID = null;
let LAST_REPORT = null;

const $ = (id) => document.getElementById(id);

function fmt(n) {
  if (n === null || n === undefined || n === "") return "";
  const num = Number(n);
  if (Number.isNaN(num)) return String(n);
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function showError(elId, msg) {
  const el = $(elId);
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearError(elId) {
  const el = $(elId);
  el.textContent = "";
  el.classList.add("hidden");
}

// ---------- Step 1: upload ----------

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError("upload-error");
  const fileInput = $("file-input");
  if (!fileInput.files.length) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);

  const btn = e.target.querySelector("button");
  btn.disabled = true;
  btn.textContent = "Uploading...";
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      showError("upload-error", data.error || "Upload failed.");
      return;
    }
    SESSION_ID = data.session_id;
    populateConfig(data);
  } catch (err) {
    showError("upload-error", "Could not reach the server: " + err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Upload & detect sheets";
  }
});

function populateConfig(data) {
  const sofpSel = $("sofp-sheet"), tbSel = $("tb-sheet"), glSel = $("gl-sheet");
  [sofpSel, tbSel, glSel].forEach((sel) => (sel.innerHTML = ""));
  data.sheet_names.forEach((name) => {
    [sofpSel, tbSel, glSel].forEach((sel) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
  });
  if (data.guessed.sofp) sofpSel.value = data.guessed.sofp;
  if (data.guessed.tb) tbSel.value = data.guessed.tb;
  if (data.guessed.gl) glSel.value = data.guessed.gl;

  $("config-section").classList.remove("hidden");
  $("report-section").classList.add("hidden");
}

// ---------- Step 2: analyze ----------

$("analyze-btn").addEventListener("click", async () => {
  clearError("analyze-error");
  const fd = new FormData();
  fd.append("session_id", SESSION_ID);
  fd.append("sofp_sheet", $("sofp-sheet").value);
  fd.append("tb_sheet", $("tb-sheet").value);
  fd.append("gl_sheet", $("gl-sheet").value);
  fd.append("tolerance", $("tolerance").value || "1");

  const btn = $("analyze-btn");
  btn.disabled = true;
  btn.textContent = "Running...";
  try {
    const res = await fetch("/api/analyze", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      showError("analyze-error", data.error || "Analysis failed.");
      return;
    }
    renderMeta(data);
    renderReport(data.report);
    $("report-section").classList.remove("hidden");
  } catch (err) {
    showError("analyze-error", "Could not reach the server: " + err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run reconciliation";
  }
});

function renderMeta(data) {
  const m = data.sofp_meta;
  $("meta-note").textContent =
    `Using the ${m.current_year} column on the SOFP sheet as the reported closing balance ` +
    `(years found on that sheet: ${(m.years_seen || []).join(", ")}). ` +
    `TB accounts detected: ${data.tb_meta.header_row ? "yes" : "no"}. ` +
    `GL accounts detected: ${data.gl_meta.account_order ? data.gl_meta.account_order.length : 0}.`;
}

// ---------- Step 3: report rendering ----------

function renderReport(report) {
  LAST_REPORT = report;

  const cards = [
    ["SOFP lines", report.summary.total_sofp_lines],
    ["Lines mapped to TB", report.summary.lines_mapped],
    ["Lines unmapped", report.summary.lines_unmapped],
    ["Lines flagged", report.summary.lines_flagged],
    ["Unmapped TB accounts", report.summary.unmapped_tb_accounts],
    ["TB accounts (total)", report.summary.total_tb_accounts],
    ["GL accounts (total)", report.summary.total_gl_accounts],
  ];
  $("summary-cards").innerHTML = cards
    .map((c) => `<div class="stat"><div class="num">${c[1]}</div><div class="label">${c[0]}</div></div>`)
    .join("");

  $("export-summary").href = `/api/export/${SESSION_ID}/summary.csv`;
  $("export-gl-detail").href = `/api/export/${SESSION_ID}/gl-detail.csv`;

  const tbody = $("lines-tbody");
  tbody.innerHTML = "";
  report.lines.forEach((line, idx) => {
    const statusClass = line.status === "OK" ? "ok" : line.status === "UNMAPPED" ? "unmapped" : "review";
    const row = document.createElement("tr");
    row.className = "line-row";
    row.innerHTML = `
      <td>&#9656;</td>
      <td>${escapeHtml(line.label)}</td>
      <td class="mono">${line.sofp_error ? `<em>${escapeHtml(line.sofp_error)}</em>` : fmt(line.sofp_value)}</td>
      <td class="mono">${fmt(line.tb_total)}</td>
      <td class="mono">${fmt(line.gl_total)}</td>
      <td class="mono">${fmt(line.diff_sofp_tb)}</td>
      <td class="mono">${fmt(line.diff_tb_gl)}</td>
      <td><span class="badge ${statusClass}">${line.status}</span></td>
    `;
    const detailRow = document.createElement("tr");
    detailRow.className = "detail-row hidden";
    const td = document.createElement("td");
    td.colSpan = 8;
    td.appendChild(buildDetail(line));
    detailRow.appendChild(td);

    row.addEventListener("click", () => detailRow.classList.toggle("hidden"));

    tbody.appendChild(row);
    tbody.appendChild(detailRow);
  });

  renderUnmapped(report);
  renderSectionRows(report);
}

function buildDetail(line) {
  const wrap = document.createElement("div");
  wrap.className = "detail-wrap";

  if (line.flags.length) {
    const ul = document.createElement("ul");
    ul.className = "flags";
    line.flags.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f;
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
  }

  if (line.tb_accounts.length) {
    const table = document.createElement("table");
    table.className = "tb-subtable";
    table.innerHTML = `
      <thead><tr>
        <th>TB account</th><th>Code</th><th>TB closing</th>
        <th>GL account</th><th>GL closing</th><th>GL txns</th><th></th>
      </tr></thead>
      <tbody></tbody>`;
    const tbody = table.querySelector("tbody");
    line.tb_accounts.forEach((tb) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(tb.tb_name)}</td>
        <td>${tb.tb_code || ""}</td>
        <td class="mono">${fmt(tb.tb_closing)}</td>
        <td>${tb.gl_account ? escapeHtml(tb.gl_account) : "<em>no GL match</em>"}</td>
        <td class="mono">${tb.gl_closing !== null ? fmt(tb.gl_closing) : ""}</td>
        <td>${tb.gl_transaction_count || 0}</td>
        <td></td>
      `;
      const lastTd = tr.querySelector("td:last-child");
      if (tb.gl_account) {
        const btn = document.createElement("button");
        btn.className = "link-btn";
        btn.textContent = "view transactions";
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          openGlModal(tb.gl_account);
        });
        lastTd.appendChild(btn);
      }
      const unmapBtn = document.createElement("button");
      unmapBtn.className = "link-btn";
      unmapBtn.style.marginLeft = "10px";
      unmapBtn.textContent = "unmap";
      unmapBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await remapTb(tb.tb_name, "");
      });
      lastTd.appendChild(unmapBtn);

      tbody.appendChild(tr);
    });
    wrap.appendChild(table);
  }

  const addRow = document.createElement("div");
  addRow.className = "add-tb-row";
  addRow.style.marginTop = "10px";
  const select = document.createElement("select");
  const unmapped = LAST_REPORT.unmapped_tb_accounts;
  if (unmapped.length) {
    select.innerHTML = `<option value="">Add unmapped TB account...</option>` +
      unmapped.map((t) => `<option value="${escapeAttr(t.name)}">${escapeHtml(t.name)} (${fmt(t.closing)})</option>`).join("");
    const btn = document.createElement("button");
    btn.className = "secondary";
    btn.textContent = "Add to this line";
    btn.style.marginLeft = "8px";
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (select.value) await remapTb(select.value, line.label);
    });
    addRow.appendChild(select);
    addRow.appendChild(btn);
    wrap.appendChild(addRow);
  }

  return wrap;
}

async function remapTb(tbName, targetLabel) {
  const fd = new FormData();
  fd.append("session_id", SESSION_ID);
  fd.append("tb_name", tbName);
  fd.append("target_label", targetLabel);
  const res = await fetch("/api/remap", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not update mapping.");
    return;
  }
  renderReport(data.report);
}

function renderUnmapped(report) {
  const tbody = $("unmapped-tbody");
  tbody.innerHTML = "";
  if (!report.unmapped_tb_accounts.length) {
    tbody.innerHTML = `<tr><td colspan="4"><em>All TB accounts are mapped to a SOFP line.</em></td></tr>`;
    return;
  }
  report.unmapped_tb_accounts.forEach((t) => {
    const tr = document.createElement("tr");
    const select = document.createElement("select");
    select.innerHTML = `<option value="">Choose a SOFP line...</option>` +
      report.all_sofp_labels.map((l) => `<option value="${escapeAttr(l)}">${escapeHtml(l)}</option>`).join("");
    const btn = document.createElement("button");
    btn.className = "secondary";
    btn.textContent = "Assign";
    btn.addEventListener("click", async () => {
      if (select.value) await remapTb(t.name, select.value);
    });
    tr.innerHTML = `<td>${escapeHtml(t.name)}</td><td>${t.code || ""}</td><td class="mono">${fmt(t.closing)}</td>`;
    const actionTd = document.createElement("td");
    actionTd.appendChild(select);
    actionTd.appendChild(btn);
    tr.appendChild(actionTd);
    tbody.appendChild(tr);
  });
}

function renderSectionRows(report) {
  const list = $("section-rows-list");
  list.innerHTML = "";
  if (!report.section_rows.length) {
    list.innerHTML = "<li><em>None detected.</em></li>";
    return;
  }
  report.section_rows.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s.label;
    list.appendChild(li);
  });
}

// ---------- GL drill-down modal ----------

async function openGlModal(account) {
  const res = await fetch(`/api/gl-transactions/${SESSION_ID}?account=${encodeURIComponent(account)}`);
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not load GL transactions.");
    return;
  }
  $("gl-modal-title").textContent = `GL account: ${account}`;
  $("gl-modal-summary").textContent =
    `Computed closing balance (opening + debits - credits): ${fmt(data.computed_closing)}` +
    (data.stated_closing !== null ? ` | Last running balance shown in GL: ${fmt(data.stated_closing)}` : "");
  const tbody = $("gl-modal-tbody");
  tbody.innerHTML = "";
  data.transactions.forEach((t) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.date ? escapeHtml(t.date) : ""}</td>
      <td>${escapeHtml(t.description || "")}</td>
      <td class="mono">${t.debit ? fmt(t.debit) : ""}</td>
      <td class="mono">${t.credit ? fmt(t.credit) : ""}</td>
      <td class="mono">${t.stated_balance !== null ? fmt(t.stated_balance) : ""}</td>
    `;
    tbody.appendChild(tr);
  });
  $("gl-modal").classList.remove("hidden");
}

$("gl-modal-close").addEventListener("click", () => $("gl-modal").classList.add("hidden"));
$("gl-modal").addEventListener("click", (e) => {
  if (e.target.id === "gl-modal") $("gl-modal").classList.add("hidden");
});

// ---------- helpers ----------

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function escapeAttr(s) {
  return escapeHtml(s);
}
