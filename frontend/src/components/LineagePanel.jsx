export default function LineagePanel({ lineage, loading, error, onClose }) {
  if (!loading && !error && !lineage) return null;

  return (
    <aside className="lineage-panel">
      <div className="lineage-header">
        <h3>Lineage</h3>
        <button type="button" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {loading && <div className="banner">Loading…</div>}
      {error && <div className="banner banner-error">{error}</div>}

      {!loading && !error && lineage && (
        <>
          <dl className="lineage-record">
            <dt>Column</dt>
            <dd>{lineage.record.column_name}</dd>
            <dt>Original</dt>
            <dd>{lineage.record.original_value ?? <em>null</em>}</dd>
            <dt>Cleaned</dt>
            <dd>{lineage.record.cleaned_value ?? <em>null</em>}</dd>
            <dt>Confidence</dt>
            <dd>{lineage.record.confidence_score.toFixed(0)}%</dd>
            <dt>Agent</dt>
            <dd>{lineage.record.agent_type}</dd>
            <dt>Timestamp</dt>
            <dd>{new Date(lineage.record.created_at).toLocaleString()}</dd>
          </dl>

          <h4>Pipeline history</h4>
          {!lineage.has_lineage && (
            <div className="banner banner-empty" data-testid="no-lineage">
              No lineage recorded for this cell.
            </div>
          )}
          {lineage.has_lineage && (
            <ol className="lineage-history">
              {lineage.history.map((entry) => (
                <li key={entry.log_id}>
                  <strong>{entry.agent_name}</strong> — {entry.action}
                  {entry.details && <pre className="lineage-details">{entry.details}</pre>}
                  <span className="lineage-timestamp">{new Date(entry.timestamp).toLocaleString()}</span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </aside>
  );
}
