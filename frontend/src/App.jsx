import { useCallback, useEffect, useState } from "react";
import "./App.css";
import { getCleanedRecords, getLineage, listUploads, submitCorrection, uploadFile } from "./api";
import CleanedRecordsTable from "./components/CleanedRecordsTable";
import InsightsView from "./components/InsightsView";
import LineagePanel from "./components/LineagePanel";
import ProcessingSummary from "./components/ProcessingSummary";
import UploadZone from "./components/UploadZone";

const PAGE_SIZE = 25;

function App() {
  const [uploads, setUploads] = useState([]);
  const [selectedUploadId, setSelectedUploadId] = useState(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [lastUploadResult, setLastUploadResult] = useState(null);
  const [activeTab, setActiveTab] = useState("results"); // "results" | "insights"

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

  const refreshPage = useCallback(() => {
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

  useEffect(refreshPage, [refreshPage]);

  function handleFile(file) {
    if (!file) return;
    setUploadBusy(true);
    setUploadError(null);
    setLastUploadResult(null);
    uploadFile(file)
      .then((result) => {
        refreshUploads();
        setLastUploadResult(result);
        setSelectedUploadId(result.upload.upload_id);
        setActiveTab("results");
        setColumnFilter("");
        setThresholdFilter("");
        setOffset(0);
        setSelectedRecordId(null);
        setLineage(null);
      })
      .catch((err) => setUploadError(err.message))
      .finally(() => setUploadBusy(false));
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

  function handleSubmitCorrection(correctedValue) {
    return submitCorrection(selectedUploadId, selectedRecordId, correctedValue).then(() => {
      refreshPage();
      return getLineage(selectedUploadId, selectedRecordId).then(setLineage);
    });
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>AI Data Cleaning &amp; Insight Platform</h1>
        <p>Upload a messy CSV, review confidence-scored cleaning, and see the findings underneath.</p>
      </header>

      <UploadZone busy={uploadBusy} onFile={handleFile} />

      <div className="toolbar">
        <select
          value={selectedUploadId ?? ""}
          onChange={(e) => {
            setSelectedUploadId(e.target.value ? Number(e.target.value) : null);
            setLastUploadResult(null);
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

        {selectedUploadId && (
          <nav className="tab-bar">
            <button
              type="button"
              className={activeTab === "results" ? "tab-active" : ""}
              onClick={() => setActiveTab("results")}
            >
              Results
            </button>
            <button
              type="button"
              className={activeTab === "insights" ? "tab-active" : ""}
              onClick={() => setActiveTab("insights")}
            >
              AI Insights
            </button>
          </nav>
        )}
      </div>

      {uploadError && <div className="banner banner-error">{uploadError}</div>}

      {lastUploadResult && activeTab === "results" && (
        <ProcessingSummary result={lastUploadResult} onDismiss={() => setLastUploadResult(null)} />
      )}

      {!selectedUploadId && (
        <div className="banner">Upload a CSV or select an existing upload to get started.</div>
      )}

      {selectedUploadId && activeTab === "results" && (
        <main className="main-layout">
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

          <LineagePanel
            lineage={lineage}
            loading={lineageLoading}
            error={lineageError}
            onClose={() => {
              setSelectedRecordId(null);
              setLineage(null);
            }}
            onSubmitCorrection={handleSubmitCorrection}
          />
        </main>
      )}

      {selectedUploadId && activeTab === "insights" && <InsightsView uploadId={selectedUploadId} />}
    </div>
  );
}

export default App;
