import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
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
import ConfirmDialog from "./components/ConfirmDialog";
import UploadZone from "./components/UploadZone";
import { DEFAULT_SORT, PAGE_SIZE } from "./constants";
import useAutoDismiss from "./hooks/useAutoDismiss";

// Each tab's page lives in its own file and is only downloaded when the
// user actually navigates there — three separate chunks instead of one
// large bundle.
const ResultsPage = lazy(() => import("./pages/ResultsPage"));
const InsightsPage = lazy(() => import("./pages/InsightsPage"));
const RulesPage = lazy(() => import("./pages/RulesPage"));

function App() {
  const navigate = useNavigate();

  const [uploads, setUploads] = useState([]);
  const [selectedUploadId, setSelectedUploadId] = useState(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [lastUploadResult, setLastUploadResult] = useState(null);

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
        navigate("/results");
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
    navigate("/results");
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

  function navLinkClass({ isActive }) {
    return isActive ? "tab-active" : "";
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
            <NavLink to="/results" className={navLinkClass}>
              Results
            </NavLink>
            <NavLink to="/insights" className={navLinkClass}>
              AI Insights
            </NavLink>
            <NavLink to="/rules" className={navLinkClass}>
              Rules
            </NavLink>
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

      {!selectedUploadId && (
        <div className="banner">Upload a CSV or select an existing upload to get started.</div>
      )}

      <Suspense fallback={<div className="banner">Loading…</div>}>
        <Routes>
          <Route
            path="/results"
            element={
              <ResultsPage
                selectedUploadId={selectedUploadId}
                lastUploadResult={lastUploadResult}
                onDismissUploadResult={() => setLastUploadResult(null)}
                page={page}
                pageLoading={pageLoading}
                pageError={pageError}
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
                offset={offset}
                onOffsetChange={setOffset}
                lineage={lineage}
                lineageLoading={lineageLoading}
                lineageError={lineageError}
                onCloseLineage={() => {
                  setSelectedRecordId(null);
                  setLineage(null);
                }}
                onSubmitCorrection={handleSubmitCorrection}
              />
            }
          />
          <Route
            path="/insights"
            element={<InsightsPage selectedUploadId={selectedUploadId} onSelectRecord={handleSelectRecordFromInsights} />}
          />
          <Route
            path="/rules"
            element={<RulesPage selectedUploadId={selectedUploadId} columns={columns} onRulesApplied={refreshPage} />}
          />
          <Route path="*" element={<Navigate to="/results" replace />} />
        </Routes>
      </Suspense>

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
