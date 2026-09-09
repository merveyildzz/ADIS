import { useEffect, useState } from "react";

export default function LineagePanel({ lineage, loading, error, onClose, onSubmitCorrection }) {
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  useEffect(() => {
    setDraft(lineage?.record?.cleaned_value ?? "");
    setSubmitError(null);
  }, [lineage?.record?.record_id]);

  if (!loading && !error && !lineage) return null;

  function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    Promise.resolve(onSubmitCorrection(draft))
      .catch((err) => setSubmitError(err.message))
      .finally(() => setSubmitting(false));
  }

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

          <form className="correction-form" onSubmit={handleSubmit}>
            <label htmlFor="correction-input">Correct this value</label>
            <div className="correction-row">
              <input
                id="correction-input"
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={submitting}
              />
              <button type="submit" disabled={submitting || draft === lineage.record.cleaned_value}>
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
            <p className="correction-hint">
              Saved corrections are reused automatically for identical values found later.
            </p>
            {submitError && <div className="banner banner-error">{submitError}</div>}
          </form>

          <h4>Pipeline history</h4>
          {!lineage.has_lineage && (
            <div className="banner banner-empty" data-testid="no-lineage">
              No lineage recorded for this cell.
            </div>
          )}
          {lineage.has_lineage && (
            <ol className="lineage-history">
              {lineage.history.map((entry) => (
                <li key={entry.log_id} className={entry.agent_name === "RuleEngine" ? "lineage-rule-violation" : ""}>
                  {entry.agent_name === "RuleEngine" && <span className="lineage-rule-tag">Rule</span>}
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
