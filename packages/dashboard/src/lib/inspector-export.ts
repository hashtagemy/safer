/**
 * Deterministic InspectorReport -> {JSON, Markdown, HTML} export.
 *
 * All rendering happens in the browser; no backend endpoint is involved.
 * The user picks which sections to include and which format; we build
 * the content and trigger a download via a Blob URL.
 */

import type {
  ASTSummary,
  Finding,
  InspectorPersona,
  InspectorReport,
  PatternMatch,
  PolicySuggestion,
} from "./inspector-types";

export type ExportSection =
  | "header"
  | "ast"
  | "patterns"
  | "findings"
  | "suggestions"
  | "files";

export type ExportFormat = "json" | "markdown" | "html";

export interface ExportSelections {
  sections: Record<ExportSection, boolean>;
  format: ExportFormat;
}

export const DEFAULT_SELECTIONS: ExportSelections = {
  sections: {
    header: true,
    ast: true,
    patterns: false,
    findings: true,
    suggestions: true,
    files: false,
  },
  format: "markdown",
};

export const SECTION_LABELS: Record<ExportSection, string> = {
  header: "Header (risk score + mode)",
  ast: "AST summary",
  patterns: "Pattern matches",
  findings: "Findings (grouped by persona)",
  suggestions: "Policy suggestions",
  files: "Scanned files",
};

const PERSONA_LABEL: Record<InspectorPersona, string> = {
  security_auditor: "Security Auditor",
  compliance_officer: "Compliance Officer",
  policy_warden: "Policy Warden",
};

export interface ExportPayload {
  filename: string;
  mimeType: string;
  content: string;
}

export function buildExport(
  report: InspectorReport,
  selections: ExportSelections
): ExportPayload {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..+$/, "");
  const base = `inspector-report-${report.agent_id}-${stamp}`;

  if (selections.format === "json") {
    return {
      filename: `${base}.json`,
      mimeType: "application/json",
      content: toJson(report, selections),
    };
  }
  if (selections.format === "markdown") {
    return {
      filename: `${base}.md`,
      mimeType: "text/markdown;charset=utf-8",
      content: toMarkdown(report, selections),
    };
  }
  return {
    filename: `${base}.html`,
    mimeType: "text/html;charset=utf-8",
    content: toHtml(report, selections),
  };
}

export function triggerBrowserDownload(payload: ExportPayload): void {
  const blob = new Blob([payload.content], { type: payload.mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = payload.filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke in a microtask so Safari actually kicks off the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---------- JSON ----------

function toJson(report: InspectorReport, sel: ExportSelections): string {
  const out: Record<string, unknown> = {
    agent_id: report.agent_id,
    generated_at: new Date().toISOString(),
  };
  if (sel.sections.header) {
    out.header = {
      risk_score: report.risk_score,
      risk_level: report.risk_level,
      duration_ms: report.duration_ms,
      persona_review_mode: report.persona_review_mode ?? null,
      persona_review_skipped: report.persona_review_skipped,
      persona_review_error: report.persona_review_error,
    };
  }
  if (sel.sections.ast) out.ast_summary = report.ast_summary;
  if (sel.sections.patterns) out.pattern_matches = report.pattern_matches;
  if (sel.sections.findings)
    out.findings = report.findings.filter((f) => f.persona != null);
  if (sel.sections.suggestions)
    out.policy_suggestions = report.policy_suggestions;
  if (sel.sections.files) out.scanned_files = report.scanned_files;
  return JSON.stringify(out, null, 2);
}

// ---------- Markdown ----------

function toMarkdown(report: InspectorReport, sel: ExportSelections): string {
  const lines: string[] = [];
  lines.push(`# Inspector report — ${report.agent_id}`);
  lines.push("");
  lines.push(`_Generated: ${new Date().toISOString()}_`);
  lines.push("");

  if (sel.sections.header) {
    lines.push("## Overview");
    lines.push("");
    lines.push(`- **Risk score:** ${report.risk_score}/100`);
    lines.push(`- **Risk level:** ${report.risk_level}`);
    lines.push(`- **Duration:** ${formatDuration(report.duration_ms)}`);
    if (report.persona_review_mode) {
      lines.push(`- **Persona review mode:** \`${report.persona_review_mode}\``);
    }
    if (report.persona_review_error) {
      lines.push(`- **Persona review error:** ${report.persona_review_error}`);
    }
    lines.push("");
  }

  if (sel.sections.ast) {
    lines.push("## AST summary");
    lines.push("");
    appendAstMarkdown(lines, report.ast_summary);
  }

  if (sel.sections.findings) {
    const persona = report.findings.filter((f) => f.persona != null);
    lines.push(`## Findings (${persona.length})`);
    lines.push("");
    for (const p of Object.keys(PERSONA_LABEL) as InspectorPersona[]) {
      const list = persona.filter((f) => f.persona === p);
      lines.push(`### ${PERSONA_LABEL[p]} — ${list.length}`);
      lines.push("");
      if (list.length === 0) {
        lines.push("_No findings from this persona._");
        lines.push("");
        continue;
      }
      for (const f of list) {
        appendFindingMarkdown(lines, f);
      }
    }
  }

  if (sel.sections.patterns) {
    lines.push(`## Pattern matches (${report.pattern_matches.length})`);
    lines.push("");
    if (report.pattern_matches.length === 0) {
      lines.push("_No deterministic pattern hits._");
      lines.push("");
    }
    for (const m of report.pattern_matches) {
      appendPatternMarkdown(lines, m);
    }
  }

  if (sel.sections.suggestions) {
    lines.push(`## Policy suggestions (${report.policy_suggestions.length})`);
    lines.push("");
    if (report.policy_suggestions.length === 0) {
      lines.push("_No suggestions._");
      lines.push("");
    }
    for (const s of report.policy_suggestions) {
      appendSuggestionMarkdown(lines, s);
    }
  }

  if (sel.sections.files) {
    lines.push(`## Scanned files (${report.scanned_files.length})`);
    lines.push("");
    for (const f of report.scanned_files) {
      lines.push(`- \`${f}\``);
    }
    lines.push("");
  }

  return lines.join("\n");
}

function appendAstMarkdown(lines: string[], ast: ASTSummary) {
  lines.push(`- **Module:** \`${ast.module || "(unknown)"}\``);
  lines.push(`- **Lines of code:** ${ast.loc}`);
  lines.push(`- **Entry points:** ${ast.entry_points.join(", ") || "—"}`);
  lines.push(`- **Tools:** ${ast.tools.map((t) => t.name).join(", ") || "—"}`);
  lines.push(
    `- **LLM calls:** ${ast.llm_calls.length > 0 ? ast.llm_calls.join(", ") : "—"}`
  );
  if (ast.parse_error) {
    lines.push(`- **Parse error:** ${ast.parse_error}`);
  }
  lines.push("");
}

function appendFindingMarkdown(lines: string[], f: Finding) {
  lines.push(`- **[${f.severity}] ${f.title}**`);
  lines.push(`  - flag: \`${f.flag}\` · category: \`${f.category}\``);
  if (f.file_path) lines.push(`  - file: \`${f.file_path}\``);
  if (f.description) lines.push(`  - ${f.description}`);
  if (f.evidence.length > 0) {
    lines.push(`  - evidence:`);
    for (const e of f.evidence.slice(0, 5)) {
      lines.push(`    - ${codeQuote(e)}`);
    }
  }
  if (f.recommended_mitigation) {
    lines.push(`  - mitigation: ${f.recommended_mitigation}`);
  }
  lines.push("");
}

function appendPatternMarkdown(lines: string[], m: PatternMatch) {
  const loc = m.file_path ? `${m.file_path}:${m.line}` : `line ${m.line}`;
  lines.push(`- **[${m.severity}] ${m.rule_id}** (${loc})`);
  lines.push(`  - flag: \`${m.flag}\``);
  lines.push(`  - ${m.message}`);
  if (m.snippet) {
    lines.push("  - snippet:");
    lines.push("    ```");
    for (const line of m.snippet.split("\n")) {
      lines.push(`    ${line}`);
    }
    lines.push("    ```");
  }
  lines.push("");
}

function appendSuggestionMarkdown(lines: string[], s: PolicySuggestion) {
  lines.push(`- **[${s.severity}] ${s.name}**`);
  lines.push(`  - reason: ${s.reason}`);
  lines.push(`  - natural language: ${s.natural_language}`);
  if (s.triggering_flags.length > 0) {
    lines.push(
      `  - triggering flags: ${s.triggering_flags.map((f) => `\`${f}\``).join(", ")}`
    );
  }
  lines.push("");
}

function codeQuote(s: string): string {
  return "`" + s.replace(/`/g, "\\`") + "`";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${rem}s`;
}

// ---------- HTML ----------

function toHtml(report: InspectorReport, sel: ExportSelections): string {
  const body = toMarkdown(report, sel);
  // Lightweight inline render — the user can rely on this for printing
  // (browser Save-as-PDF). No external CSS, dark-text on white for print.
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Inspector report — ${escapeHtml(report.agent_id)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 820px; margin: 2rem auto; padding: 0 1rem; color: #0f172a;
           line-height: 1.5; }
    h1, h2, h3 { color: #0b1220; }
    h1 { font-size: 1.6rem; border-bottom: 1px solid #cbd5e1; padding-bottom: .3rem; }
    h2 { font-size: 1.25rem; margin-top: 1.6rem; }
    h3 { font-size: 1.05rem; margin-top: 1.2rem; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                font-size: 0.85rem; }
    pre { background: #f1f5f9; padding: 0.6rem; border-radius: 4px; overflow-x: auto; }
    ul { padding-left: 1.3rem; }
    li { margin: 0.2rem 0; }
    em { color: #475569; }
  </style>
</head>
<body>
${markdownToHtml(body)}
</body>
</html>`;
}

// Minimal markdown -> HTML (headings, bullets, em, code fences, inline code).
// Good enough for this report; we control the generator so there's no need
// for a full markdown lib.
function markdownToHtml(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inList = false;
  let inCode = false;
  let codeBuf: string[] = [];
  for (const raw of lines) {
    const line = raw;
    if (line.trim().startsWith("```")) {
      if (inCode) {
        out.push(
          `<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`
        );
        codeBuf = [];
        inCode = false;
      } else {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }
    if (line.startsWith("# ")) {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      out.push(`<h1>${inlineMd(line.slice(2))}</h1>`);
      continue;
    }
    if (line.startsWith("## ")) {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      out.push(`<h2>${inlineMd(line.slice(3))}</h2>`);
      continue;
    }
    if (line.startsWith("### ")) {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      out.push(`<h3>${inlineMd(line.slice(4))}</h3>`);
      continue;
    }
    const bullet = line.match(/^(\s*)- (.*)$/);
    if (bullet) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inlineMd(bullet[2])}</li>`);
      continue;
    }
    if (line.trim() === "") {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      continue;
    }
    // Italic-only line (used for "No findings" placeholder)
    if (line.startsWith("_") && line.endsWith("_")) {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      out.push(`<p><em>${escapeHtml(line.slice(1, -1))}</em></p>`);
      continue;
    }
    out.push(`<p>${inlineMd(line)}</p>`);
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}

function inlineMd(s: string): string {
  // Escape first, then apply simple inline transforms so our own markers
  // aren't mangled by HTML escaping.
  let e = escapeHtml(s);
  e = e.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  e = e.replace(/`([^`]+?)`/g, "<code>$1</code>");
  return e;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
