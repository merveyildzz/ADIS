import { useCallback, useEffect, useState } from "react";
import "./App.css";
import {
  deleteUpload,
  downloadCleanedCsv,
  getCleanedRecords,
  getColumns,
  getLineage,
  listUploads,
  submitCorrection,
  uploadFile,
} from "./api";
import CleanedRecordsTable from "./components/CleanedRecordsTable";
import ConfirmDialog from "./components/ConfirmDialog";
import InsightsView from "./components/InsightsView";
import LineagePanel from "./components/LineagePanel";
import ProcessingSummary from "./components/ProcessingSummary";
import RuleManager from "./components/rules/RuleManager";
import UploadZone from "./components/UploadZone";
import useAutoDismiss from "./hooks/useAutoDismiss";

const PAGE_SIZE = 25;
const DEFAULT_SORT = "record_id:asc";

function App() {
  const [uploads, setUploads] = useState([]);
  const [selectedUploadId, setSelectedUploadId] = useState(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [lastUploadResult, setLastUploadResult] = useState(null);
  const [activeTab, setActiveTab] = useState("results"); // "results" | "insights"

  const [columns, setColumns] = useState([]);
  const [columnFilter, setColumnFilter] = useState("");
  const [thresholdFilter, setThresholdFilter] = useState("");
  const [sortValue, setSortValue] = useState(DEFAULT_SORT);
  const [offset, setOffset] = useState(0);

  const [page, setPage] = useState(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState(null);

  const [selectedRecordId, setSelectedRecordId] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [lineageError, setLineageError] = useState(null);

  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState(null);

  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [pendingDeleteUploadId, setPendingDeleteUploadId] = useState(null);

  useAutoDismiss(uploadError, () => setUploadError(null));
  useAutoDismiss(downloadError, () => setDownloadError(null));
  useAutoDismiss(deleteError, () => setDeleteError(null));

  function refreshUploads() {
    listUploads()
      .then(setUploads)
      .catch((err) => setUploadError(err.message));
  }

  useEffect(refreshUploads, []);

  useEffect(() => {
    if (!selectedUploadId) {
      setColumns([]);
      return;
    }
    getColumns(selectedUploadId)
      .then(setColumns)
      .catch(() => setColumns([])); // the column dropdown just stays empty; not fatal
  }, [selectedUploadId]);

  const refreshPage = useCallback(() => {
    if (!selectedUploadId) {
      setPage(null);
      return;
    }
    const [sortBy, sortDir] = sortValue.split(":");
    setPageLoading(true);
    setPageError(null);
    getCleanedRecords(selectedUploadId, {
      columnName: columnFilter || undefined,
      maxConfidence: thresholdFilter,
      sortBy,
      sortDir,
      limit: PAGE_SIZE,
      offset,
    })
      .then(setPage)
      .catch((err) => setPageError(err.message))
      .finally(() => setPageLoading(false));
  }, [selectedUploadId, columnFilter, thresholdFilter, sortValue, offset]);

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
        setSortValue(DEFAULT_SORT);
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

  function handleSelectRecordFromInsights(recordId) {
    setActiveTab("results");
    handleSelectRecord(recordId);
  }

  function handleSubmitCorrection(correctedValue) {
    return submitCorrection(selectedUploadId, selectedRecordId, correctedValue).then(() => {
      refreshPage();
      return getLineage(selectedUploadId, selectedRecordId).then(setLineage);
    });
  }

  function requestDeleteUpload(uploadId) {
    setPendingDeleteUploadId(uploadId);
  }

  function cancelDeleteUpload() {
    setPendingDeleteUploadId(null);
  }

  function confirmDeleteUpload() {
    const uploadId = pendingDeleteUploadId;
    setPendingDeleteUploadId(null);
    setDeleteBusy(true);
    setDeleteError(null);
    deleteUpload(uploadId)
      .then(() => {
        refreshUploads();
        if (selectedUploadId === uploadId) {
          setSelectedUploadId(null);
          setLastUploadResult(null);
          setColumnFilter("");
          setSortValue(DEFAULT_SORT);
          setOffset(0);
          setSelectedRecordId(null);
          setLineage(null);
        }
      })
      .catch((err) => setDeleteError(err.message))
      .finally(() => setDeleteBusy(false));
  }

  const pendingDeleteUpload = uploads.find((u) => u.upload_id === pendingDeleteUploadId);

  function handleDownload() {
    const upload = uploads.find((u) => u.upload_id === selectedUploadId);
    setDownloadBusy(true);
    setDownloadError(null);
    downloadCleanedCsv(selectedUploadId, upload ? `cleaned_${upload.filename}` : undefined)
      .catch((err) => setDownloadError(err.message))
      .finally(() => setDownloadBusy(false));
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>ADIS</h1>
        <p>Upload a messy CSV, review confidence-scored cleaning, and see the findings underneath.</p>
      </header>

      <UploadZone busy={uploadBusy} onFile={handleFile} />

      <div className="toolbar">
        <select
          value={selectedUploadId ?? ""}
          onChange={(e) => {
            setSelectedUploadId(e.target.value ? Number(e.target.value) : null);
            setLastUploadResult(null);
            setColumnFilter("");
            setSortValue(DEFAULT_SORT);
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
          <button
            type="button"
            className="delete-upload-button"
            onClick={() => requestDeleteUpload(selectedUploadId)}
            disabled={deleteBusy}
            title="Delete this upload"
          >
            {deleteBusy ? "Deleting…" : "Delete upload"}
          </button>
        )}

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
            <button
              type="button"
              className={activeTab === "rules" ? "tab-active" : ""}
              onClick={() => setActiveTab("rules")}
            >
              Rules
            </button>
          </nav>
        )}

        {selectedUploadId && (
          <button type="button" className="download-button" onClick={handleDownload} disabled={downloadBusy}>
            {downloadBusy ? "Preparing…" : "Download cleaned CSV"}
          </button>
        )}
      </div>

      {uploadError && <div className="banner banner-error">{uploadError}</div>}
      {downloadError && <div className="banner banner-error">{downloadError}</div>}
      {deleteError && <div className="banner banner-error">{deleteError}</div>}

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
            columns={columns}
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
            sortValue={sortValue}
            onSortChange={(v) => {
              setSortValue(v);
              setOffset(0);
            }}
            onSelectRecord={handleSelectRecord}
            selectedRecordId={selectedRecordId}
            onPrevPage={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            onNextPage={() => setOffset((o) => o + PAGE_SIZE)}
            onGoToPage={(p) => setOffset((p - 1) * PAGE_SIZE)}
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

      {selectedUploadId && activeTab === "insights" && (
        <InsightsView uploadId={selectedUploadId} onSelectRecord={handleSelectRecordFromInsights} />
      )}

      {selectedUploadId && activeTab === "rules" && <RuleManager columns={columns} />}

      <ConfirmDialog
        open={pendingDeleteUploadId !== null}
        title="Delete upload"
        message={`Delete upload "${pendingDeleteUpload?.filename ?? pendingDeleteUploadId}"? This removes all its cleaned data and cannot be undone.`}
        confirmLabel="Delete"
        danger
        onConfirm={confirmDeleteUpload}
        onCancel={cancelDeleteUpload}
      />
    </div>
  );
}

export default App;
