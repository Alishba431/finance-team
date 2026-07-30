/* SOFP Tracer front end - plain DOM, no build step. */

const state = {
  year: null,
  tolerance: 1,
  trace: null,
  headOptions: [],
  expanded: new Set(),
};

/* ---------------------------------------------------------------- helpers */

const $ = (selector) => document.querySelector(selector);

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "&mdash;";
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function diffCell(value, tolerance) {
  if (value === null || value === undefined) return '<td class="num muted">&mdash;</td>';
  const bad = Math.abs(value) > (tolerance ?? 1);
  return `<td class="num ${bad ? "diff-bad" : "diff-ok"}">${money(value)}</td>`;
}

function statusPill(status) {
  const map = {
    tie: ["ok", "Agrees"],
    difference: ["bad", "Difference"],
    "no-accounts": ["warn", "No accounts mapped"],
    "no-fs-figure": ["mute", "No FS figure"],
  };
  const [cls, text] = map[status] || ["mute", status];
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function confidencePill(account) {
  if (account.map_source === "user") return '<span class="pill ok">Set by you</span>';
  if (account.map_source === "stale-override")
    return '<span class="pill warn">Saved head no longer exists</span>';
  if (account.map_source === "none") return '<span class="pill bad">Unmapped</span>';
  const cls =
    account.map_confidence === "high" ? "mute" : account.map_confidence === "medium" ? "warn" : "bad";
  return `<span class="pill ${cls}">Auto &middot; ${esc(account.map_confidence)}</span>`;
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (error) {
      /* keep the default message */
    }
    throw new Error(detail);
  }
  return response.json();
}

function queryString(extra = {}) {
  const params = new URLSearchParams({ tolerance: String(state.tolerance), ...extra });
  if (state.year) params.set("year", state.year);
  return params.toString();
}

/* ------------------------------------------------------------------ tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "review") loadReview();
  });
});

/* --------------------------------------------------------------- sources */

function renderSources(payload) {
  document.querySelectorAll(".source-state").forEach((element) => {
    const source = payload.sources[element.dataset.source];
    element.classList.remove("loaded", "error");
    if (source.error) {
      element.textContent = source.error;
      element.classList.add("error");
    } else if (source.loaded) {
      element.textContent = `Loaded: ${source.file ? source.file.name : "workbook"} (sheet "${source.sheet}")`;
      element.classList.add("loaded");
    } else {
      element.textContent = "Not loaded";
    }
  });

  const parts = [];
  if (payload.notes && payload.notes.length) {
    parts.push(
      `<h2>Startup</h2><ul class="note-list">${payload.notes
        .map((note) => `<li>${esc(note)}</li>`)
        .join("")}</ul>`
    );
  }
  if (payload.warnings && payload.warnings.length) {
    parts.push(
      `<h2>Parsing notes</h2><ul class="note-list">${payload.warnings
        .map((warning) => `<li><strong>${esc(warning.source)}:</strong> ${esc(warning.message)}</li>`)
        .join("")}</ul>`
    );
  }
  $("#source-detail").innerHTML = parts.join("");
}

async function refreshState() {
  const payload = await api("/api/state");
  renderSources(payload);

  const select = $("#year-select");
  const years = payload.years || [];
  if (years.length) {
    const previous = state.year;
    select.innerHTML = years
      .map((year) => {
        const partial = year.partial
          ? ` (partial: ${year.coverage}/${year.line_count} lines)`
          : "";
        return `<option value="${esc(year.label)}">${esc(year.label)}${esc(partial)}</option>`;
      })
      .join("");
    state.year = years.some((y) => y.label === previous) ? previous : years[0].label;
    select.value = state.year;
    select.disabled = false;
  } else {
    select.innerHTML = '<option value="">&mdash;</option>';
    select.disabled = true;
  }
  return payload;
}

/* ----------------------------------------------------------------- trace */

function renderSummary(summary, counts) {
  const cards = [
    {
      label: `Heads agreeing (${summary.year_label || "-"})`,
      value: `${summary.heads_tied} / ${summary.head_count}`,
      sub: `${summary.heads_with_difference} with a difference, ${summary.heads_without_accounts} with nothing mapped`,
      cls: summary.heads_with_difference ? "bad" : "ok",
    },
    {
      label: "TB accounts mapped to a head",
      value: `${summary.mapped_account_count} / ${summary.tb_account_count}`,
      sub: `${summary.pl_account_count} income &amp; expenditure, ${summary.unmapped_account_count} unmapped`,
      cls: summary.unmapped_account_count ? "warn" : "ok",
    },
    {
      label: "Accounts where TB &ne; GL",
      value: String(summary.accounts_gl_out_of_balance),
      sub: `${summary.accounts_not_in_gl} mapped accounts have no ledger detail`,
      cls: summary.accounts_gl_out_of_balance ? "bad" : "ok",
    },
    {
      label: "Ledger entries read",
      value: Number(summary.gl_transaction_count || 0).toLocaleString("en-US"),
      sub: counts.gl_only ? `${counts.gl_only} GL accounts are not in the TB` : "All GL accounts appear in the TB",
      cls: "",
    },
    {
      label: "TB debits less credits",
      value: money(summary.tb_control ? summary.tb_control.closing_difference : null),
      sub: "Closing balances across the whole trial balance",
      cls:
        summary.tb_control && Math.abs(summary.tb_control.closing_difference) > state.tolerance
          ? "bad"
          : "ok",
    },
  ];

  $("#summary").innerHTML = cards
    .map(
      (card) => `<div class="card ${card.cls}">
        <div class="label">${card.label}</div>
        <div class="value">${card.value}</div>
        <div class="sub">${card.sub}</div>
      </div>`
    )
    .join("");
}

function headRowHtml(head) {
  const open = state.expanded.has(head.id);
  return `<tr class="head-row" data-head="${esc(head.id)}">
    <td><span class="caret">${open ? "&#9662;" : "&#9656;"}</span> ${esc(head.label)}
      ${head.note ? `<span class="muted small"> (note ${esc(head.note)})</span>` : ""}</td>
    <td class="num">${money(head.fs_amount)}</td>
    <td class="num">${money(head.tb_amount)}</td>
    ${diffCell(head.fs_vs_tb, state.tolerance)}
    <td class="num">${money(head.gl_amount)}</td>
    ${diffCell(head.tb_vs_gl, state.tolerance)}
    <td class="num">${head.account_count}</td>
    <td>${statusPill(head.status)}</td>
  </tr>
  <tr class="child-row" data-child="${esc(head.id)}" ${open ? "" : "hidden"}>
    <td colspan="8"><div class="child-holder">Loading&hellip;</div></td>
  </tr>`;
}

function renderTrace(payload) {
  state.trace = payload;
  state.headOptions = payload.head_options || [];
  renderSummary(payload.summary, payload.counts);

  const badge = $("#review-badge");
  const needsReview =
    payload.counts.unmapped +
    payload.summary.accounts_gl_out_of_balance +
    payload.summary.accounts_needing_review;
  badge.textContent = needsReview ? String(needsReview) : "";

  const rows = [];
  let section = null;
  payload.heads.forEach((head) => {
    if (head.section !== section) {
      section = head.section;
      rows.push(`<tr class="section-row"><td colspan="8">${esc(section)}</td></tr>`);
    }
    rows.push(headRowHtml(head));
  });

  $("#trace-table").innerHTML = `<div class="table-wrap"><table>
    <thead><tr>
      <th>SOFP head</th>
      <th class="num">Per financials</th>
      <th class="num">Per trial balance</th>
      <th class="num">FS less TB</th>
      <th class="num">Per general ledger</th>
      <th class="num">TB less GL</th>
      <th class="num">Accounts</th>
      <th>Status</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody>
  </table></div>
  <p class="hint small">Click a head to see the trial balance accounts behind it; click an account to
  see its ledger entries. Amounts are shown on the natural side of each head (assets positive,
  funds and liabilities positive).</p>`;

  $("#trace-table")
    .querySelectorAll(".head-row")
    .forEach((row) => row.addEventListener("click", () => toggleHead(row.dataset.head)));

  state.expanded.forEach((headId) => loadHeadDetail(headId));
}

async function loadTrace() {
  const message = $("#trace-message");
  try {
    const payload = await api(`/api/trace?${queryString()}`);
    message.textContent = "";
    renderTrace(payload);
  } catch (error) {
    $("#trace-table").innerHTML = "";
    $("#summary").innerHTML = "";
    message.textContent = error.message;
  }
}

function toggleHead(headId) {
  const child = document.querySelector(`tr[data-child="${CSS.escape(headId)}"]`);
  const row = document.querySelector(`tr[data-head="${CSS.escape(headId)}"]`);
  if (!child) return;
  const isOpen = state.expanded.has(headId);
  if (isOpen) {
    state.expanded.delete(headId);
    child.hidden = true;
    row.querySelector(".caret").innerHTML = "&#9656;";
  } else {
    state.expanded.add(headId);
    child.hidden = false;
    row.querySelector(".caret").innerHTML = "&#9662;";
    loadHeadDetail(headId);
  }
}

function accountRowsHtml(accounts) {
  if (!accounts.length) {
    return '<p class="message">No trial balance account is mapped to this head yet. Use the Review &amp; mapping tab to assign one.</p>';
  }
  const rows = accounts
    .map(
      (account) => `<tr class="account-row" data-key="${esc(account.key)}">
      <td>${esc(account.code || "")}</td>
      <td class="wrap-text"><button class="link" type="button">${esc(account.name)}</button></td>
      <td class="num">${money(account.tb_opening)}</td>
      <td class="num">${money(account.tb_movement_debit)}</td>
      <td class="num">${money(account.tb_movement_credit)}</td>
      <td class="num">${money(account.tb_closing)}</td>
      <td class="num">${money(account.gl_closing)}</td>
      ${diffCell(account.closing_difference, state.tolerance)}
      <td class="num">${account.gl_transaction_count || 0}</td>
      <td>${confidencePill(account)}</td>
    </tr>`
    )
    .join("");

  return `<table class="child-table">
    <thead><tr>
      <th>Code</th><th>Account</th>
      <th class="num">TB opening</th><th class="num">Period debit</th><th class="num">Period credit</th>
      <th class="num">TB closing</th><th class="num">GL closing</th><th class="num">TB less GL</th>
      <th class="num">Entries</th><th>Mapping</th>
    </tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function loadHeadDetail(headId) {
  const child = document.querySelector(`tr[data-child="${CSS.escape(headId)}"] .child-holder`);
  if (!child) return;
  try {
    const payload = await api(`/api/head?${queryString({ head_id: headId })}`);
    child.innerHTML = accountRowsHtml(payload.accounts);
    child.querySelectorAll(".account-row").forEach((row) => {
      row.addEventListener("click", (event) => {
        event.stopPropagation();
        openAccount(row.dataset.key);
      });
    });
  } catch (error) {
    child.innerHTML = `<p class="message">${esc(error.message)}</p>`;
  }
}

/* ------------------------------------------------------- account drawer */

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
}

$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);

async function openAccount(key) {
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer-title").textContent = "Loading account…";
  $("#drawer-body").innerHTML = "";

  try {
    const payload = await api(`/api/account?${queryString({ key })}`);
    const account = payload.account;
    $("#drawer-title").textContent = account.label;

    const cards = `<div class="cards">
      <div class="card"><div class="label">TB opening</div><div class="value">${money(account.tb_opening)}</div></div>
      <div class="card"><div class="label">Period movement</div><div class="value">${money(account.tb_movement)}</div>
        <div class="sub">Dr ${money(account.tb_movement_debit)} / Cr ${money(account.tb_movement_credit)}</div></div>
      <div class="card"><div class="label">TB closing</div><div class="value">${money(account.tb_closing)}</div></div>
      <div class="card ${account.ties_to_gl === false ? "bad" : account.ties_to_gl ? "ok" : ""}">
        <div class="label">GL closing</div><div class="value">${money(account.gl_closing)}</div>
        <div class="sub">${
          account.in_gl
            ? `TB less GL ${money(account.closing_difference)}`
            : "Not present in the uploaded general ledger"
        }</div></div>
    </div>`;

    const transactions = payload.transactions || [];
    let table;
    if (!transactions.length) {
      table = '<p class="message">No ledger entries were found for this account.</p>';
    } else {
      const rows = transactions
        .map(
          (entry) => `<tr>
        <td>${esc(entry.date || "")}</td>
        <td>${esc(entry.month || "")}</td>
        <td>${esc(entry.reference || "")}</td>
        <td class="wrap-text">${esc(entry.partner || "")}</td>
        <td class="wrap-text">${esc(entry.description || "")}</td>
        <td class="num">${entry.debit ? money(entry.debit) : ""}</td>
        <td class="num">${entry.credit ? money(entry.credit) : ""}</td>
        <td class="num muted">${entry.row}</td>
      </tr>`
        )
        .join("");
      const totalDebit = transactions.reduce((sum, entry) => sum + (entry.debit || 0), 0);
      const totalCredit = transactions.reduce((sum, entry) => sum + (entry.credit || 0), 0);
      table = `<div class="table-wrap"><table>
        <thead><tr><th>Date</th><th>Month</th><th>Reference</th><th>Partner</th>
          <th>Description</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">GL row</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr>
          <td colspan="5"><strong>${transactions.length} entries</strong> (opening ${money(
        payload.ledger ? payload.ledger.opening : null
      )})</td>
          <td class="num"><strong>${money(totalDebit)}</strong></td>
          <td class="num"><strong>${money(totalCredit)}</strong></td><td></td>
        </tr></tfoot>
      </table></div>`;
    }

    $("#drawer-body").innerHTML = cards + table;
  } catch (error) {
    $("#drawer-body").innerHTML = `<p class="message">${esc(error.message)}</p>`;
  }
}

/* ---------------------------------------------------------------- review */

function mapSelect(account) {
  const groups = {};
  state.headOptions.forEach((option) => {
    (groups[option.section] = groups[option.section] || []).push(option);
  });
  const optionHtml = Object.entries(groups)
    .map(
      ([section, options]) =>
        `<optgroup label="${esc(section)}">${options
          .map(
            (option) =>
              `<option value="${esc(option.id)}" ${
                option.id === account.head_id ? "selected" : ""
              }>${esc(option.label)}</option>`
          )
          .join("")}</optgroup>`
    )
    .join("");
  return `<select class="map-select" data-key="${esc(account.key)}">
    <option value="__auto__">Automatic suggestion</option>${optionHtml}</select>`;
}

function reviewTable(title, description, accounts, options = {}) {
  if (!accounts.length) {
    return `<h2>${esc(title)}</h2><p class="message">Nothing to review here.</p>`;
  }
  const rows = accounts
    .map((account) => {
      const amount =
        options.amountField && account[options.amountField] !== undefined
          ? account[options.amountField]
          : account.tb_closing !== undefined
          ? account.tb_closing
          : account.gl_closing;
      return `<tr>
        <td>${esc(account.code || "")}</td>
        <td class="wrap-text">${esc(account.label || account.name || "")}</td>
        <td class="num">${money(amount)}</td>
        ${options.extraCell ? options.extraCell(account) : ""}
        ${options.mappable ? `<td>${mapSelect(account)}</td>` : ""}
      </tr>`;
    })
    .join("");

  return `<h2>${esc(title)} <span class="muted small">(${accounts.length})</span></h2>
    <p class="hint small">${description}</p>
    <div class="table-wrap"><table><thead><tr>
      <th>Code</th><th>Account</th><th class="num">${esc(options.amountLabel || "Closing (Dr-Cr)")}</th>
      ${options.extraHeader || ""}${options.mappable ? "<th>SOFP head</th>" : ""}
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function loadReview() {
  const body = $("#review-body");
  try {
    const payload = await api(`/api/review?${queryString()}`);
    state.headOptions = payload.head_options || state.headOptions;

    const sections = [
      reviewTable(
        "Unmapped accounts",
        "These trial balance accounts are not included in any SOFP head. Assign each one to a head, " +
          "or to the income &amp; expenditure bucket if it does not belong on the balance sheet.",
        payload.unmapped,
        { mappable: true }
      ),
      reviewTable(
        "Low-confidence automatic mappings",
        "Mapped by keyword only - confirm or correct each one.",
        payload.low_confidence,
        { mappable: true }
      ),
      reviewTable(
        "Trial balance does not agree to the general ledger",
        "The closing balance per the trial balance differs from opening plus movements per the ledger.",
        payload.gl_mismatch,
        {
          amountField: "closing_difference",
          amountLabel: "TB less GL",
          extraHeader: '<th class="num">TB closing</th><th class="num">GL closing</th>',
          extraCell: (account) =>
            `<td class="num">${money(account.tb_closing)}</td><td class="num">${money(
              account.gl_closing
            )}</td>`,
        }
      ),
      reviewTable(
        "Mapped accounts with no ledger detail",
        "These accounts are in the trial balance but were not found in the uploaded general ledger, " +
          "so their balances cannot be traced to individual entries.",
        payload.missing_gl,
        {}
      ),
      reviewTable(
        "In the general ledger but not in the trial balance",
        "Ledger accounts with no matching trial balance line.",
        payload.gl_only,
        { amountField: "gl_closing", amountLabel: "GL closing" }
      ),
      reviewTable(
        "Treated as income &amp; expenditure (excluded from the SOFP)",
        "These accounts were classified as revenue or expenditure. Move any that belong on the balance sheet.",
        payload.income_expenditure,
        { mappable: true }
      ),
    ];

    body.innerHTML = sections.join("") +
      `<div class="upload-actions"><button id="reset-mapping" class="secondary" type="button">
        Reset all manual mappings</button>
        <span class="status" id="mapping-status"></span></div>`;

    body.querySelectorAll(".map-select").forEach((select) => {
      select.addEventListener("change", async () => {
        const status = $("#mapping-status");
        try {
          await api("/api/mapping", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assignments: { [select.dataset.key]: select.value } }),
          });
          status.textContent = "Mapping saved.";
          await loadTrace();
          await loadReview();
        } catch (error) {
          status.textContent = error.message;
        }
      });
    });

    const reset = $("#reset-mapping");
    if (reset) {
      reset.addEventListener("click", async () => {
        if (!window.confirm("Remove every manual mapping and fall back to the automatic suggestions?"))
          return;
        await api("/api/mapping", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reset: true }),
        });
        await loadTrace();
        await loadReview();
      });
    }
  } catch (error) {
    body.innerHTML = `<p class="message">${esc(error.message)}</p>`;
  }
}

/* --------------------------------------------------------------- wiring */

$("#year-select").addEventListener("change", (event) => {
  state.year = event.target.value || null;
  loadTrace();
});

$("#tolerance").addEventListener("change", (event) => {
  const value = Number(event.target.value);
  state.tolerance = Number.isFinite(value) && value >= 0 ? value : 1;
  event.target.value = state.tolerance;
  loadTrace();
});

$("#export-btn").addEventListener("click", () => {
  window.location.href = `/api/export.xlsx?${queryString()}`;
});

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData();
  let count = 0;
  ["financials", "trial_balance", "general_ledger"].forEach((name) => {
    const input = form.querySelector(`input[name="${name}"]`);
    if (input && input.files && input.files[0]) {
      data.append(name, input.files[0]);
      count += 1;
    }
  });

  const status = $("#upload-status");
  if (!count) {
    status.textContent = "Choose at least one workbook first.";
    return;
  }

  status.textContent = "Uploading and parsing…";
  try {
    const result = await api("/api/upload", { method: "POST", body: data });
    const stored = result.stored.map((item) => `${item.file} → ${item.label}`).join("; ");
    const rejected = result.rejected.map((item) => `${item.file}: ${item.reason}`).join("; ");
    status.textContent = [stored && `Loaded ${stored}.`, rejected].filter(Boolean).join(" ");
    form.reset();
    state.expanded.clear();
    await refreshState();
    await loadTrace();
  } catch (error) {
    status.textContent = error.message;
  }
});

$("#clear-btn").addEventListener("click", async () => {
  if (!window.confirm("Remove the loaded workbooks from the app?")) return;
  await api("/api/clear", { method: "POST" });
  state.expanded.clear();
  await refreshState();
  await loadTrace();
});

(async function init() {
  await refreshState();
  await loadTrace();
})();
