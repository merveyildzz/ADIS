import { useEffect, useState } from "react";
import "./App.css";
import { getCleanedRecords, getLineage, listUploads, uploadFile } from "./api";
import CleanedRecordsTable from "./components/CleanedRecordsTable";
import LineagePanel from "./components/LineagePanel";

const PAGE_SIZE = 25;

function App() {
  const [uploads, setUploads] = useState([]);
  const [selectedUploadId, setSelectedUploadId] = useState(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  const [columnFilter, setColumnFilter] = useState("");
  const [thresholdFilter, setThresholdFilter] = useState("");
  const [offset, setOffset] = useState(0);

  const [page, setPage] = useState(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState(null);

  const [selectedRecordId, setSelectedRecordId] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [lineageError, setLineageError] = useState(null);

  function refreshUploads() {
    listUploads()
      .then(setUploads)
      .catch((err) => setUploadError(err.message));
  }

  useEffect(refreshUploads, []);

  useEffect(() => {
    if (!selectedUploadId) {
      setPage(null);
      return;
    }
    setPageLoading(true);
    setPageError(null);
    getCleanedRecords(selectedUploadId, {
      columnName: columnFilter || undefined,
      maxConfidence: thresholdFilter,
      limit: PAGE_SIZE,
      offset,
    })
      .then(setPage)
      .catch((err) => setPageError(err.message))
      .finally(() => setPageLoading(false));
  }, [selectedUploadId, columnFilter, thresholdFilter, offset]);

  function handleFileChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadBusy(true);
    setUploadError(null);
    uploadFile(file)
      .then((result) => {
        refreshUploads();
        setSelectedUploadId(result.upload.upload_id);
        setColumnFilter("");
        setThresholdFilter("");
        setOffset(0);
        setSelectedRecordId(null);
        setLineage(null);
      })
      .catch((err) => setUploadError(err.message))
      .finally(() => {
        setUploadBusy(false);
        e.target.value = "";
      });
  }

  function handleSelectRecord(recordId) {
    setSelectedRecordId(recordId);
    setLineageLoading(true);
    setLineageError(null);
    getLineage(selectedUploadId, recordId)
      .then(setLineage)
      .catch((err) => setLineageError(err.message))
      .finally(() => setLineageLoading(false));
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Trust Heatmap</h1>
        <p>Confidence-scored cleaned data with per-cell lineage drill-down.</p>
      </header>

      <div className="toolbar">
        <label className="upload-control">
          <span>{uploadBusy ? "Uploading…" : "Upload CSV"}</span>
          <input type="file" accept=".csv" onChange={handleFileChange} disabled={uploadBusy} />
        </label>

        <select
          value={selectedUploadId ?? ""}
          onChange={(e) => {
            setSelectedUploadId(e.target.value ? Number(e.target.value) : null);
            setOffset(0);
            setSelectedRecordId(null);
            setLineage(null);
          }}
        >
          <option value="">Select an upload…</option>
          {uploads.map((u) => (
            <option key={u.upload_id} value={u.upload_id}>
              #{u.upload_id} — {u.filename} ({u.status}, {u.row_count ?? "?"} rows)
            </option>
          ))}
        </select>
      </div>

      {uploadError && <div className="banner banner-error">{uploadError}</div>}

      <main className="main-layout">
        {selectedUploadId ? (
          <CleanedRecordsTable
            page={page}
            loading={pageLoading}
            error={pageError}
            columnFilter={columnFilter}
            onColumnFilterChange={(v) => {
              setColumnFilter(v);
              setOffset(0);
            }}
            thresholdFilter={thresholdFilter}
            onThresholdFilterChange={(v) => {
              setThresholdFilter(v);
              setOffset(0);
            }}
            onSelectRecord={handleSelectRecord}
            selectedRecordId={selectedRecordId}
            onPrevPage={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            onNextPage={() => setOffset((o) => o + PAGE_SIZE)}
          />
        ) : (
          <div className="banner">Upload a CSV or select an existing upload to see the trust heatmap.</div>
        )}

        <LineagePanel
          lineage={lineage}
          loading={lineageLoading}
          error={lineageError}
          onClose={() => {
            setSelectedRecordId(null);
            setLineage(null);
          }}
        />
      </main>
    </div>
  );
}

export default App;
