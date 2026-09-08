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

export function getCleanedRecords(uploadId, { columnName, maxConfidence, limit = 25, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit, offset });
  if (columnName) params.set("column_name", columnName);
  if (maxConfidence !== undefined && maxConfidence !== null && maxConfidence !== "") {
    params.set("max_confidence", maxConfidence);
  }
  return request(`/api/uploads/${uploadId}/cleaned-records?${params.toString()}`);
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
