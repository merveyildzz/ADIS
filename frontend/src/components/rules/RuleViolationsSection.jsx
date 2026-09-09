import { useEffect, useState } from "react";
import Section from "../insights/Section";
import { getRuleViolations } from "../../api";

export default function RuleViolationsSection({ uploadId, onSelectRecord }) {
  const [violations, setViolations] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!uploadId) return;
    getRuleViolations(uploadId)
      .then(setViolations)
      .catch((err) => setError(err.message));
  }, [uploadId]);

  if (error) return <div className="banner banner-error">{error}</div>;
  if (violations.length === 0) return null;

  const byRule = new Map();
  for (const v of violations) {
    const key = v.rule_name || `Rule #${v.rule_id}`;
    if (!byRule.has(key)) byRule.set(key, []);
    byRule.get(key).push(v);
  }

  return (
    <Section title="Rule Violations" count={violations.length}>
      {[...byRule.entries()].map(([ruleName, items]) => (
        <button
          key={ruleName}
          type="button"
          className="insight-card"
          onClick={() => items[0].record_id && onSelectRecord(items[0].record_id)}
        >
          <span className="insight-card-title">{ruleName}</span>
          <span className="insight-card-body">
            {items.length} violation{items.length === 1 ? "" : "s"} — e.g. {items[0].column_name} ({items[0].severity})
          </span>
        </button>
      ))}
    </Section>
  );
}
