export default function ProcessingSummary({ result, onDismiss }) {
  if (!result) return null;
  const columns = Object.entries(result.columns_cleaned || {});

  return (
    <div className="processing-summary">
      <div className="processing-summary-header">
        <h3>Processing complete — {result.upload.filename}</h3>
        <button type="button" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      </div>
      {result.warnings?.length > 0 && (
        <div className="banner" data-testid="upload-warnings">
          {result.warnings.join(" ")}
        </div>
      )}
      <ul className="agent-log">
        {columns.map(([column, summary]) => (
          <li key={column}>
            <strong>{summary.agent_type}</strong>: {column} — {summary.rows_processed} rows processed,{" "}
            {summary.rows_flagged} flagged
            {summary.rows_influenced_by_feedback > 0 && <>, {summary.rows_influenced_by_feedback} reused from feedback</>}
          </li>
        ))}
        {result.columns_unclassified?.length > 0 && (
          <li className="agent-log-unclassified">
            Left untouched (no matching agent): {result.columns_unclassified.join(", ")}
          </li>
        )}
      </ul>
    </div>
  );
}
