const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // response wasn't JSON — keep statusText
    }
    throw new Error(detail);
  }
  return response.json();
}

export function listUploads() {
  return request("/api/uploads");
}

export function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/uploads", { method: "POST", body: form });
}

export function getCleanedRecords(
  uploadId,
  { columnName, maxConfidence, sortBy, sortDir, limit = 25, offset = 0 } = {}
) {
  const params = new URLSearchParams({ limit, offset });
  if (columnName) params.set("column_name", columnName);
  if (maxConfidence !== undefined && maxConfidence !== null && maxConfidence !== "") {
    params.set("max_confidence", maxConfidence);
  }
  if (sortBy) params.set("sort_by", sortBy);
  if (sortDir) params.set("sort_dir", sortDir);
  return request(`/api/uploads/${uploadId}/cleaned-records?${params.toString()}`);
}

export function getColumns(uploadId) {
  return request(`/api/uploads/${uploadId}/columns`);
}

export function deleteUpload(uploadId) {
  return request(`/api/uploads/${uploadId}`, { method: "DELETE" });
}

export function getLineage(uploadId, recordId) {
  return request(`/api/uploads/${uploadId}/records/${recordId}/lineage`);
}

export function submitCorrection(uploadId, recordId, correctedValue) {
  return request(`/api/uploads/${uploadId}/records/${recordId}/correction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corrected_value: correctedValue }),
  });
}

export function getInsights(uploadId) {
  return request(`/api/uploads/${uploadId}/insights`);
}

export function getConfig() {
  return request("/api/config");
}

export function listRules() {
  return request("/api/rules");
}

export function createRule(rule) {
  return request("/api/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
}

export function updateRule(ruleId, rule) {
  return request(`/api/rules/${ruleId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
}

export function deleteRule(ruleId) {
  return request(`/api/rules/${ruleId}`, { method: "DELETE" });
}

export function getRuleViolations(uploadId) {
  return request(`/api/uploads/${uploadId}/rule-violations`);
}

export async function downloadCleanedCsv(uploadId, filename) {
  const response = await fetch(`${API_BASE}/api/uploads/${uploadId}/export`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      // not JSON — keep statusText
    }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || `cleaned_upload_${uploadId}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
