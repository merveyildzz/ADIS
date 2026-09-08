import { useEffect, useState } from "react";
import { getInsights } from "../api";
import { AnomalyScatter, CategoryBar, CorrelationHeatmap, CorrelationScatter, TrendLine } from "./insights/Charts";
import InsightCards from "./insights/InsightCards";

export default function InsightsView({ uploadId }) {
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!uploadId) return;
    setLoading(true);
    setError(null);
    getInsights(uploadId)
      .then(setInsights)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [uploadId]);

  if (loading) return <div className="banner">Computing insights…</div>;
  if (error) return <div className="banner banner-error">{error}</div>;
  if (!insights) return null;

  const charts = insights.charts || {};

  return (
    <div className="insights-view">
      <InsightCards insights={insights} />

      {insights.available && (
        <div className="chart-grid">
          <TrendLine data={charts.trend_line} />
          <CorrelationScatter data={charts.correlation_scatter} />
          <CorrelationHeatmap data={charts.correlation_matrix} />
          <CategoryBar data={charts.category_bar} />
          <AnomalyScatter data={charts.anomaly_scatter} />
        </div>
      )}
    </div>
  );
}
