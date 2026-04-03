/* Lawttorney Word Add-in — Taskpane Logic */

// Initialize Office.js
Office.onReady(function (info) {
  if (info.host === Office.HostType.Word) {
    document.getElementById("statusBar").textContent = "Connected to Word";
    // Load saved settings
    var savedUrl = localStorage.getItem("lt_api_url");
    var savedKey = localStorage.getItem("lt_api_key");
    if (savedUrl) document.getElementById("apiUrl").value = savedUrl;
    if (savedKey) document.getElementById("apiKey").value = savedKey;
  }
});

// ============================================================
// Settings
// ============================================================

function toggleSettings() {
  var panel = document.getElementById("settingsPanel");
  panel.classList.toggle("show");
}

function getApiUrl() {
  var url = document.getElementById("apiUrl").value.trim().replace(/\/+$/, "");
  localStorage.setItem("lt_api_url", url);
  return url;
}

function getApiKey() {
  var key = document.getElementById("apiKey").value.trim();
  localStorage.setItem("lt_api_key", key);
  return key;
}

function getHeaders() {
  var headers = { "Content-Type": "application/json" };
  var key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

async function testConnection() {
  var status = document.getElementById("connStatus");
  status.textContent = "Testing...";
  status.className = "status-msg";
  try {
    var resp = await fetch(getApiUrl() + "/health", { headers: getHeaders() });
    if (resp.ok) {
      status.textContent = "Connected!";
      status.className = "status-msg ok";
    } else {
      status.textContent = "Error: HTTP " + resp.status;
      status.className = "status-msg err";
    }
  } catch (e) {
    status.textContent = "Failed: " + e.message;
    status.className = "status-msg err";
  }
}

// ============================================================
// Tab Switching
// ============================================================

function switchTab(btn) {
  document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function (c) { c.classList.remove("active"); });
  btn.classList.add("active");
  document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
}

// ============================================================
// Word Document Interaction
// ============================================================

async function getSelectedText() {
  return Word.run(async function (context) {
    var selection = context.document.getSelection();
    selection.load("text");
    await context.sync();
    return selection.text.trim();
  });
}

async function insertTextAtCursor(text) {
  return Word.run(async function (context) {
    var selection = context.document.getSelection();
    // Insert as OOXML for formatting, or plain text
    selection.insertText(text, Word.InsertLocation.replace);
    await context.sync();
  });
}

async function insertHtmlAtCursor(html) {
  return Word.run(async function (context) {
    var selection = context.document.getSelection();
    selection.insertHtml(html, Word.InsertLocation.after);
    await context.sync();
  });
}

// ============================================================
// API Calls
// ============================================================

async function callApi(query) {
  var resp = await fetch(getApiUrl() + "/search", {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify({ Promptquery: query }),
  });
  if (!resp.ok) {
    var err = await resp.json().catch(function () { return {}; });
    throw new Error(err.message || "API error: HTTP " + resp.status);
  }
  return resp.json();
}

async function callExport(rawText, format) {
  var resp = await fetch(getApiUrl() + "/export", {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify({ raw_text: rawText, format: format }),
  });
  if (!resp.ok) {
    throw new Error("Export failed: HTTP " + resp.status);
  }
  return resp.blob();
}

// ============================================================
// Simple Markdown to HTML renderer
// ============================================================

function mdToHtml(md) {
  var html = md;
  // Code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
    return "<pre><code>" + escapeHtml(code) + "</code></pre>";
  });
  // Headings
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // Bold, italic
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Inline code
  html = html.replace(/`(.+?)`/g, "<code>$1</code>");
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  // Bullet lists
  html = html.replace(/^[\-*+] (.+)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
  // Ordered lists
  html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");
  // Tables
  html = html.replace(/^\|(.+)\|$/gm, function (match) {
    if (/^\|[\s\-:|]+\|$/.test(match)) return "";
    var cells = match.replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); });
    return "<tr>" + cells.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
  });
  html = html.replace(/(<tr>.*<\/tr>\s*)+/g, "<table>$&</table>");
  // HR
  html = html.replace(/^[-*_]{3,}$/gm, "<hr>");
  // Paragraphs (double newline)
  html = html.replace(/\n\n+/g, "</p><p>");
  html = "<p>" + html + "</p>";
  // Clean empty paragraphs
  html = html.replace(/<p>\s*<\/p>/g, "");
  return html;
}

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ============================================================
// Status & UI Helpers
// ============================================================

function setStatus(msg, type) {
  var bar = document.getElementById("statusBar");
  bar.textContent = msg;
  bar.className = "status-bar" + (type ? " " + type : "");
}

function setLoading(btnId, loading) {
  var btn = document.getElementById(btnId);
  if (loading) {
    btn.disabled = true;
    btn._origText = btn.textContent;
    btn.innerHTML = '<span class="loading-spinner"></span> Working...';
  } else {
    btn.disabled = false;
    btn.textContent = btn._origText || "Go";
  }
}

function showResult(resultId, actionsId, html, rawMarkdown) {
  var area = document.getElementById(resultId);
  area.innerHTML = html;
  area.classList.add("has-content");
  area.dataset.markdown = rawMarkdown || "";
  if (actionsId) {
    document.getElementById(actionsId).style.display = "flex";
  }
}

// ============================================================
// Research
// ============================================================

async function getSelectedAndResearch() {
  try {
    var text = await getSelectedText();
    if (text) {
      document.getElementById("researchQuery").value = text;
      setStatus("Selection loaded: " + text.substring(0, 50) + "...");
    } else {
      setStatus("No text selected in document", "error");
    }
  } catch (e) {
    setStatus("Error reading selection: " + e.message, "error");
  }
}

async function doResearch() {
  var query = document.getElementById("researchQuery").value.trim();
  if (!query) { setStatus("Enter a research query", "error"); return; }

  setLoading("researchBtn", true);
  setStatus("Researching...");
  try {
    var data = await callApi(query);
    var result = data.result || "No results found.";
    var html = mdToHtml(result);
    showResult("researchResult", "researchActions", html, result);
    setStatus("Research complete — " + data.agents_used.join(", "), "success");
  } catch (e) {
    setStatus("Research failed: " + e.message, "error");
  } finally {
    setLoading("researchBtn", false);
  }
}

// ============================================================
// Draft
// ============================================================

async function doDraft() {
  var query = document.getElementById("draftQuery").value.trim();
  if (!query) { setStatus("Describe what you want to draft", "error"); return; }

  var docType = document.getElementById("draftType").value;
  if (docType) {
    query = "Draft a " + docType.replace(/_/g, " ") + ": " + query;
  }

  setLoading("draftBtn", true);
  setStatus("Generating draft...");
  try {
    var data = await callApi(query);
    var result = data.result || "Draft generation failed.";
    var html = mdToHtml(result);
    showResult("draftResult", "draftActions", html, result);
    setStatus("Draft ready — " + result.length + " chars", "success");
  } catch (e) {
    setStatus("Draft failed: " + e.message, "error");
  } finally {
    setLoading("draftBtn", false);
  }
}

// ============================================================
// Review
// ============================================================

async function getSelectedForReview() {
  try {
    var text = await getSelectedText();
    if (text) {
      document.getElementById("reviewText").value = text;
      setStatus("Loaded " + text.length + " chars from selection");
    } else {
      setStatus("No text selected in document", "error");
    }
  } catch (e) {
    setStatus("Error reading selection: " + e.message, "error");
  }
}

async function doReview() {
  var text = document.getElementById("reviewText").value.trim();
  if (!text) { setStatus("Select or paste text to review", "error"); return; }

  var checks = [];
  if (document.getElementById("checkRisks").checked) checks.push("risk identification");
  if (document.getElementById("checkCompliance").checked) checks.push("compliance check");
  if (document.getElementById("checkImprove").checked) checks.push("improvement suggestions");

  var query = "Review the following legal text for " + checks.join(", ") +
    ". Identify issues, flag risks, and provide specific recommendations.\n\nText to review:\n" + text;

  setLoading("reviewBtn", true);
  setStatus("Reviewing...");
  try {
    var data = await callApi(query);
    var result = data.result || "Review complete — no issues found.";
    var html = mdToHtml(result);
    showResult("reviewResult", "reviewActions", html, result);
    setStatus("Review complete", "success");
  } catch (e) {
    setStatus("Review failed: " + e.message, "error");
  } finally {
    setLoading("reviewBtn", false);
  }
}

// ============================================================
// Cite
// ============================================================

async function doCite() {
  var query = document.getElementById("citeQuery").value.trim();
  if (!query) { setStatus("Enter a case name or statute to cite", "error"); return; }

  var fullQuery = "Find and provide the full legal citation for: " + query +
    ". Include case number, court, date, bench, and a 2-line summary.";

  setLoading("citeBtn", true);
  setStatus("Finding citation...");
  try {
    var data = await callApi(fullQuery);
    var result = data.result || "Citation not found.";
    var html = mdToHtml(result);
    showResult("citeResult", "citeActions", html, result);
    setStatus("Citation found", "success");
  } catch (e) {
    setStatus("Citation search failed: " + e.message, "error");
  } finally {
    setLoading("citeBtn", false);
  }
}

// ============================================================
// Actions (Insert, Export, Copy)
// ============================================================

async function insertResult(resultId) {
  var area = document.getElementById(resultId);
  var markdown = area.dataset.markdown || area.innerText;
  if (!markdown.trim()) { setStatus("No content to insert", "error"); return; }

  try {
    setStatus("Inserting into document...");
    // Try HTML insert for better formatting
    var html = area.innerHTML;
    await insertHtmlAtCursor(html);
    setStatus("Inserted into document", "success");
  } catch (e) {
    // Fallback to plain text
    try {
      await insertTextAtCursor(markdown);
      setStatus("Inserted as plain text", "success");
    } catch (e2) {
      setStatus("Insert failed: " + e2.message, "error");
    }
  }
}

async function exportResult(resultId, format) {
  var area = document.getElementById(resultId);
  var markdown = area.dataset.markdown || area.innerText;
  if (!markdown.trim()) { setStatus("No content to export", "error"); return; }

  setStatus("Generating " + format.toUpperCase() + "...");
  try {
    var blob = await callExport(markdown, format);
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "lawttorney_export." + format;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setStatus("Exported as " + format.toUpperCase(), "success");
  } catch (e) {
    setStatus("Export failed: " + e.message, "error");
  }
}

function copyResult(resultId) {
  var area = document.getElementById(resultId);
  var markdown = area.dataset.markdown || area.innerText;
  navigator.clipboard.writeText(markdown).then(function () {
    setStatus("Copied to clipboard", "success");
  }).catch(function () {
    setStatus("Copy failed", "error");
  });
}
