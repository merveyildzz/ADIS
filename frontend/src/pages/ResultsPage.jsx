import CleanedRecordsTable from "../components/CleanedRecordsTable";
import LineagePanel from "../components/LineagePanel";
import ProcessingSummary from "../components/ProcessingSummary";
import { PAGE_SIZE } from "../constants";

export default function ResultsPage({
  selectedUploadId,
  lastUploadResult,
  onDismissUploadResult,
  page,
  pageLoading,
  pageError,
  columns,
  columnFilter,
  onColumnFilterChange,
  thresholdFilter,
  onThresholdFilterChange,
  sortValue,
  onSortChange,
  onSelectRecord,
  selectedRecordId,
  onOffsetChange,
  offset,
  lineage,
  lineageLoading,
  lineageError,
  onCloseLineage,
  onSubmitCorrection,
}) {
  if (!selectedUploadId) return null;

  return (
    <>
      {lastUploadResult && <ProcessingSummary result={lastUploadResult} onDismiss={onDismissUploadResult} />}

      <main className="main-layout">
        <CleanedRecordsTable
          page={page}
          loading={pageLoading}
          error={pageError}
          columns={columns}
          columnFilter={columnFilter}
          onColumnFilterChange={onColumnFilterChange}
          thresholdFilter={thresholdFilter}
          onThresholdFilterChange={onThresholdFilterChange}
          sortValue={sortValue}
          onSortChange={onSortChange}
          onSelectRecord={onSelectRecord}
          selectedRecordId={selectedRecordId}
          onPrevPage={() => onOffsetChange(Math.max(0, offset - PAGE_SIZE))}
          onNextPage={() => onOffsetChange(offset + PAGE_SIZE)}
          onGoToPage={(p) => onOffsetChange((p - 1) * PAGE_SIZE)}
        />

        <LineagePanel
          lineage={lineage}
          loading={lineageLoading}
          error={lineageError}
          onClose={onCloseLineage}
          onSubmitCorrection={onSubmitCorrection}
        />
      </main>
    </>
  );
}
