import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

const COLOR_ACCENT = "#2f6fed";
const COLOR_LOW = "#b3261e";
const COLOR_MUTED = "#9aa4b2";

function heatmapColor(r) {
  if (r === null || r === undefined) return "#eef0f3";
  const abs = Math.min(1, Math.abs(r));
  const alpha = 0.15 + abs * 0.75;
  return r >= 0 ? `rgba(26, 127, 55, ${alpha})` : `rgba(179, 38, 30, ${alpha})`;
}

function ChartCard({ title, subtitle, children, empty }) {
  return (
    <div className="chart-card">
      <h4>{title}</h4>
      {subtitle && <p className="chart-subtitle">{subtitle}</p>}
      {empty ? <div className="banner banner-empty">Not enough data for this chart.</div> : children}
    </div>
  );
}

export function CorrelationHeatmap({ data }) {
  if (!data || !data.columns?.length) {
    return <ChartCard title="Correlation matrix" empty />;
  }
  return (
    <ChartCard title="Correlation matrix" subtitle="Pairwise Pearson r across numeric columns">
      <div className="heatmap-matrix-wrap">
        <table className="heatmap-matrix">
          <thead>
            <tr>
              <th />
              {data.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.columns.map((rowCol, i) => (
              <tr key={rowCol}>
                <th>{rowCol}</th>
                {data.columns.map((colCol, j) => {
                  const r = data.values[i][j];
                  return (
                    <td key={colCol} style={{ background: heatmapColor(r) }} title={`${rowCol} vs ${colCol}`}>
                      {r === null ? "–" : r.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

export function CorrelationScatter({ data }) {
  if (!data || !data.points?.length) {
    return <ChartCard title="Relationship" empty />;
  }
  return (
    <ChartCard title="Relationship" subtitle={`${data.col1} vs ${data.col2} (with trend line)`}>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="x" name={data.col1} />
          <YAxis type="number" dataKey="y" name={data.col2} />
          <ZAxis range={[20, 20]} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} />
          <Scatter data={data.points} fill={COLOR_ACCENT} />
          {data.trendline && (
            <Line
              data={data.trendline}
              dataKey="y"
              stroke={COLOR_LOW}
              strokeWidth={2}
              dot={false}
              activeDot={false}
              legendType="none"
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function TrendLine({ data }) {
  if (!data || !data.series?.length) {
    return <ChartCard title="Trend" empty />;
  }
  return (
    <ChartCard title="Trend" subtitle={`Monthly total — ${data.column}`}>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data.series} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="period" tick={{ fontSize: 11 }} />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke={COLOR_ACCENT} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CategoryBar({ data }) {
  if (!data || !data.categories?.length) {
    return <ChartCard title="Category comparison" empty />;
  }
  return (
    <ChartCard title="Category comparison" subtitle={`Average ${data.value_column} by category`}>
      <ResponsiveContainer width="100%" height={340}>
        <BarChart data={data.categories} margin={{ top: 10, right: 20, bottom: 80, left: 30 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="category" angle={-30} textAnchor="end" interval={0} tick={{ fontSize: 11 }} />
          <YAxis />
          <Tooltip />
          <Legend verticalAlign="bottom" wrapperStyle={{ paddingTop: 40 }} />
          <Bar dataKey="avg_value" name="avg value" fill={COLOR_ACCENT} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function AnomalyScatter({ data }) {
  if (!data || !data.points?.length) {
    return <ChartCard title="Anomalies" empty />;
  }
  return (
    <ChartCard title="Anomalies" subtitle={`${data.column} by row`}>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="row_index" name="row" />
          <YAxis type="number" dataKey="value" name={data.column} />
          <ZAxis range={[16, 60]} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} />
          <Scatter data={data.points}>
            {data.points.map((p) => (
              <Cell key={p.row_index} fill={p.is_anomaly ? COLOR_LOW : COLOR_MUTED} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
