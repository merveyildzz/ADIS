const HIGH_THRESHOLD = 90;
const MEDIUM_THRESHOLD = 60;

const SORT_OPTIONS = [
  { value: "record_id:asc", label: "Default order" },
  { value: "column_name:asc", label: "Column (A → Z)" },
  { value: "column_name:desc", label: "Column (Z → A)" },
  { value: "confidence_score:asc", label: "Confidence (low → high)" },
  { value: "confidence_score:desc", label: "Confidence (high → low)" },
  { value: "cleaned_value:asc", label: "Cleaned value (A → Z)" },
  { value: "cleaned_value:desc", label: "Cleaned value (Z → A)" },
];

function confidenceClass(score) {
  if (score >= HIGH_THRESHOLD) return "confidence-high";
  if (score >= MEDIUM_THRESHOLD) return "confidence-medium";
  return "confidence-low";
}

// Windowed page list with `null` standing in for an ellipsis, e.g.
// [1, null, 4, 5, 6, null, 12] — keeps the pager short for large datasets.
function buildPageList(current, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages = new Set([1, totalPages, current, current - 1, current + 1]);
  const sorted = [...pages].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b);
  const withGaps = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) withGaps.push(null);
    withGaps.push(p);
  });
  return withGaps;
}

export default function CleanedRecordsTable({
  page,
  loading,
  error,
  columns,
  columnFilter,
  onColumnFilterChange,
  thresholdFilter,
  onThresholdFilterChange,
  sortValue,
  onSortChange,
  onSelectRecord,
  selectedRecordId,
  onPrevPage,
  onNextPage,
  onGoToPage,
}) {
  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  const limit = page?.limit ?? 25;
  const offset = page?.offset ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.floor(offset / limit) + 1;

  return (
    <div className="heatmap-panel">
      <div className="filters">
        <label>
          Column
          <select value={columnFilter} onChange={(e) => onColumnFilterChange(e.target.value)}>
            <option value="">All columns</option>
            {(columns ?? []).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Show only cells below confidence
          <input
            type="number"
            min="0"
            max="100"
            placeholder="no filter"
            value={thresholdFilter}
            onChange={(e) => onThresholdFilterChange(e.target.value)}
          />
        </label>
        <label>
          Sort by
          <select value={sortValue} onChange={(e) => onSortChange(e.target.value)}>
            {SORT_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <div className="legend">
          <span className="legend-swatch confidence-high" /> High (≥{HIGH_THRESHOLD})
          <span className="legend-swatch confidence-medium" /> Medium ({MEDIUM_THRESHOLD}-{HIGH_THRESHOLD - 1})
          <span className="legend-swatch confidence-low" /> Low (&lt;{MEDIUM_THRESHOLD})
        </div>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {loading && <div className="banner">Loading…</div>}

      {!loading && !error && items.length === 0 && (
        <div className="banner banner-empty" data-testid="empty-state">
          {thresholdFilter !== ""
            ? `No cells found below confidence ${thresholdFilter}. Nothing needs review here.`
            : "No cleaned records for this upload yet."}
        </div>
      )}

      {!loading && items.length > 0 && (
        <>
          <table className="heatmap-table">
            <thead>
              <tr>
                <th>Column</th>
                <th>Original</th>
                <th>Cleaned</th>
                <th>Confidence</th>
                <th>Agent</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.record_id}
                  className={item.record_id === selectedRecordId ? "row-selected" : ""}
                  onClick={() => onSelectRecord(item.record_id)}
                >
                  <td>{item.column_name}</td>
                  <td className="value-cell">{item.original_value ?? <em>null</em>}</td>
                  <td className={`value-cell ${confidenceClass(item.confidence_score)}`}>
                    {item.cleaned_value ?? <em>null</em>}
                  </td>
                  <td className={confidenceClass(item.confidence_score)}>
                    {item.confidence_score.toFixed(0)}
                  </td>
                  <td>{item.agent_type}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pagination">
            <button type="button" onClick={onPrevPage} disabled={offset === 0}>
              ← Prev
            </button>
            <span className="pagination-pages">
              {buildPageList(currentPage, totalPages).map((p, i) =>
                p === null ? (
                  <span key={`gap-${i}`} className="pagination-ellipsis">
                    …
                  </span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    className={p === currentPage ? "pagination-page-active" : ""}
                    onClick={() => onGoToPage(p)}
                    aria-current={p === currentPage ? "page" : undefined}
                  >
                    {p}
                  </button>
                )
              )}
            </span>
            <button type="button" onClick={onNextPage} disabled={offset + limit >= total}>
              Next →
            </button>
            <span className="pagination-summary">
              {offset + 1}-{Math.min(offset + limit, total)} of {total}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
