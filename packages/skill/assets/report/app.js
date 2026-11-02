const state = { audit: null };

const labels = {
  "review-unused": "覆盖记录中未观察到",
  "review-low-use": "低频使用",
  "review-duplicate": "重复启用",
  keep: "建议保留",
  unknown: "证据不足",
};

const tierLabels = {
  frequent: "频繁使用",
  occasional: "偶尔使用",
  "not-detected": "未检测到使用",
};

const windowLabels = {
  "7d": "近 7 天",
  "30d": "近 30 天",
  "90d": "近 90 天",
};

function loadAudit() {
  if (!window.__LOCAL_TRACKER_AUDIT__) throw new Error("报告未包含盘点数据");
  state.audit = window.__LOCAL_TRACKER_AUDIT__;
  render();
}

function render() {
  const { audit } = state;
  const history = audit.history_coverage || {};
  const coverage = audit.coverage || {};
  document.querySelector("#window-label").textContent = windowLabels[audit.window] || audit.window || "未知";
  document.querySelector("#coverage").textContent = history.start
    ? `已扫描会话日志覆盖 ${history.start.slice(0, 10)} 至 ${history.end.slice(0, 10)}，共 ${history.session_count} 个 session；当前 ${audit.window} 分析窗口覆盖 ${coverage.session_count || 0} 个 session`
    : "当前没有可观测的本地会话记录";
  const summary = audit.summary || {};
  const usageSummary = audit.usage_summary || {};
  const summaryRoot = document.querySelector("#summary");
  summaryRoot.replaceChildren();
  for (const [label, value, tier] of [
    ["已盘点", summary.total || 0, "total"],
    ["频繁使用", usageSummary.frequent || 0, "frequent"],
    ["偶尔使用", usageSummary.occasional || 0, "occasional"],
    ["未检测到", usageSummary.not_detected || 0, "not-detected"],
  ]) {
    const metric = document.createElement("div");
    metric.className = `metric ${tier}`;
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    const span = document.createElement("span");
    span.textContent = label;
    metric.append(strong, span);
    summaryRoot.append(metric);
  }

  const filtered = filterRows(audit.capabilities || []);
  renderUsageGroups(filtered);
}

function filterRows(rows) {
  const type = document.querySelector("#type-filter").value;
  const client = document.querySelector("#client-filter").value;
  return rows.filter((row) =>
    (type === "all" || row.type === type) &&
    (client === "all" || (row.clients || []).includes(client))
  );
}

function renderUsageGroups(rows) {
  const root = document.querySelector("#usage-groups");
  root.replaceChildren();
  if (!rows.length) {
    root.innerHTML = '<div class="empty">当前筛选下没有能力。</div>';
    return;
  }
  for (const tier of ["frequent", "occasional", "not-detected"]) {
    const groupRows = rows.filter((row) => row.usage_tier === tier);
    const details = document.createElement("details");
    details.className = `usage-group ${tier}`;
    details.open = true;
    const summary = document.createElement("summary");
    const title = document.createElement("strong");
    title.textContent = tierLabels[tier];
    const count = document.createElement("span");
    count.textContent = `${groupRows.length} 项`;
    summary.append(title, count);
    const list = document.createElement("div");
    list.className = "capability-list";
    if (!groupRows.length) {
      list.innerHTML = '<div class="empty">当前筛选下没有此等级的能力。</div>';
    } else {
      for (const row of groupRows) list.append(renderCapability(row));
    }
    details.append(summary, list);
    root.append(details);
  }
}

function renderCapability(row) {
  const node = document.querySelector("#capability-template").content.cloneNode(true);
  node.querySelector("h3").textContent = row.name;
  node.querySelector(".badges").innerHTML = [
    `<span class="badge">${escapeHtml(row.type)}</span>`,
    `<span class="badge">${escapeHtml((row.clients || []).join(" + "))}</span>`,
    `<span class="badge">${escapeHtml(tierLabels[row.usage_tier] || row.usage_tier)}</span>`,
    `<span class="badge ${String(row.recommendation).startsWith("review-") ? "review" : ""}">${escapeHtml(labels[row.recommendation] || row.recommendation)}</span>`,
  ].join("");
  node.querySelector(".description").textContent = recommendationCopy(row);
  node.querySelector("dl").innerHTML = [
    ["使用", `${row.use_count || 0} 次`],
    ["频率", `${Number(row.uses_per_10_days || 0).toFixed(2)} 次 / 10 天`],
    ["会话", `${row.session_count || 0} 个`],
    ["最后观察", row.last_observed_use ? row.last_observed_use.slice(0, 10) : "未观察到"],
    ["来源", row.ownership || "unknown"],
    ["路径", row.path || row.config_source || "未提供"],
  ].map(([key, value]) => `<div><dt>${key}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  return node;
}

function recommendationCopy(row) {
  if (row.recommendation === "review-unused") {
    return "在当前可观测记录中未检测到使用。此结论不代表它从未使用。";
  }
  if (row.recommendation === "review-low-use") return "当前窗口内只观察到少量使用，建议确认是否仍需启用。";
  if (row.recommendation === "review-duplicate") return "同一客户端的多个位置启用了同名能力，建议检查是否重复。";
  if (row.recommendation === "keep") return "当前窗口内有持续使用证据。";
  return "来源、状态或日志覆盖不足，暂不建议执行操作。";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[char]));
}

document.querySelector("#type-filter").addEventListener("change", render);
document.querySelector("#client-filter").addEventListener("change", render);
try {
  loadAudit();
} catch (error) {
  document.querySelector("#coverage").textContent = "报告加载失败";
  document.querySelector("#summary").innerHTML = "";
  document.querySelector("#usage-groups").innerHTML = `<div class="empty error">${escapeHtml(error.message)}。请检查 Local Tracker CLI 后刷新页面。</div>`;
}
