import { useState } from "react";

function relationshipCard(c, onOpen) {
  return (
    <button key={`${c.col1}-${c.col2}`} type="button" className="insight-card" onClick={() => onOpen(c, "correlation")}>
      <span className="insight-card-icon">🔗</span>
      <span className="insight-card-title">Relationship</span>
      <span className="insight-card-body">
        {c.col1} and {c.col2}: r = {c.r} ({c.strength})
      </span>
    </button>
  );
}

function trendCard(t, onOpen) {
  const label = t.dimension === "category_count" ? `${t.label} orders` : t.label;
  const verb = t.direction === "increase" ? "increased" : "decreased";
  return (
    <button key={`${t.dimension}-${t.label}-${t.period}`} type="button" className="insight-card" onClick={() => onOpen(t, "trend")}>
      <span className="insight-card-icon">📈</span>
      <span className="insight-card-title">Trend</span>
      <span className="insight-card-body">
        {label} {verb} {Math.abs(t.change_pct)}% in {t.period}.
      </span>
    </button>
  );
}

function anomalyCard(a, onOpen) {
  return (
    <button key={`${a.column}-${a.row_index}`} type="button" className="insight-card" onClick={() => onOpen(a, "anomaly")}>
      <span className="insight-card-icon">⚠</span>
      <span className="insight-card-title">Anomaly</span>
      <span className="insight-card-body">
        Row {a.row_index}: {a.column} is {Math.abs(a.z_score)}σ {a.direction} normal.
      </span>
    </button>
  );
}

export default function InsightCards({ insights }) {
  const [open, setOpen] = useState(null); // { item, kind }

  if (!insights.available) {
    return (
      <div className="banner banner-empty" data-testid="insights-unavailable">
        Insights haven't been computed for this upload yet.
      </div>
    );
  }

  const hasAny = insights.correlations.length || insights.trends.length || insights.anomalies.length;

  return (
    <div>
      {insights.warnings.length > 0 && (
        <div className="banner" data-testid="insights-warnings">
          {insights.warnings.join(" ")}
        </div>
      )}

      {!hasAny && (
        <div className="banner banner-empty">
          No correlations, trends, or anomalies met the significance thresholds for this dataset.
        </div>
      )}

      <div className="insight-card-grid">
        {insights.trends.map((t) => trendCard(t, (item, kind) => setOpen({ item, kind })))}
        {insights.correlations.map((c) => relationshipCard(c, (item, kind) => setOpen({ item, kind })))}
        {insights.anomalies.map((a) => anomalyCard(a, (item, kind) => setOpen({ item, kind })))}
      </div>

      {open && (
        <div className="explain-overlay" onClick={() => setOpen(null)}>
          <div className="explain-modal" onClick={(e) => e.stopPropagation()}>
            <div className="explain-header">
              <h3>Why does this matter?</h3>
              <button type="button" onClick={() => setOpen(null)} aria-label="Close">
                ×
              </button>
            </div>
            <p className="explain-narrative">{open.item.narrative}</p>
            <dl className="explain-stats">
              {open.kind === "correlation" && (
                <>
                  <dt>Correlation</dt>
                  <dd>r = {open.item.r}</dd>
                </>
              )}
              {open.kind === "trend" && (
                <>
                  <dt>Change</dt>
                  <dd>
                    {open.item.baseline} → {open.item.period_value} ({open.item.change_pct > 0 ? "+" : ""}
                    {open.item.change_pct}%)
                  </dd>
                </>
              )}
              {open.kind === "anomaly" && (
                <>
                  <dt>Deviation</dt>
                  <dd>{open.item.z_score}σ {open.item.direction} the normal range</dd>
                </>
              )}
              <dt>Source</dt>
              <dd>{open.item.narrative_method === "llm_generated" ? "AI-phrased" : "Template (rule-based)"}</dd>
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}
