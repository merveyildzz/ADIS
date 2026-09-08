const HIGH_THRESHOLD = 90;
const MEDIUM_THRESHOLD = 60;

function confidenceClass(score) {
  if (score >= HIGH_THRESHOLD) return "confidence-high";
  if (score >= MEDIUM_THRESHOLD) return "confidence-medium";
  return "confidence-low";
}

export default function CleanedRecordsTable({
  page,
  loading,
  error,
  columnFilter,
  onColumnFilterChange,
  thresholdFilter,
  onThresholdFilterChange,
  onSelectRecord,
  selectedRecordId,
  onPrevPage,
  onNextPage,
}) {
  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  const limit = page?.limit ?? 25;
  const offset = page?.offset ?? 0;

  return (
    <div className="heatmap-panel">
      <div className="filters">
        <label>
          Column
          <input
            type="text"
            placeholder="e.g. order_date"
            value={columnFilter}
            onChange={(e) => onColumnFilterChange(e.target.value)}
          />
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
            <span>
              {offset + 1}-{Math.min(offset + limit, total)} of {total}
            </span>
            <button type="button" onClick={onNextPage} disabled={offset + limit >= total}>
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  );
}
