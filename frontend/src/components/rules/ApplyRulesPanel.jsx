import { useEffect, useState } from "react";
import { getAppliedRules, listRules, setAppliedRules } from "../../api";

export default function ApplyRulesPanel({ uploadId, onApplied }) {
  const [rules, setRules] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!uploadId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    Promise.all([listRules({ activeOnly: true }), getAppliedRules(uploadId)])
      .then(([allRules, applied]) => {
        setRules(allRules);
        setSelected(new Set(applied.rule_ids));
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [uploadId]);

  function toggle(ruleId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ruleId)) {
        next.delete(ruleId);
      } else {
        next.add(ruleId);
      }
      return next;
    });
  }

  function handleApply() {
    setBusy(true);
    setError(null);
    setResult(null);
    setAppliedRules(uploadId, [...selected])
      .then(() => {
        setResult({ appliedCount: selected.size });
        onApplied?.();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  }

  if (!uploadId || loading) return null;

  return (
    <div className="apply-rules-panel">
      <h3>Apply rules to this upload</h3>
      <p className="rule-manager-hint">
        Rule definitions above are reusable across datasets, but each dataset decides for itself
        which ones actually apply — every excel file can have a different set of active rules.
        Check the rules relevant to this upload, then apply.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      {rules.length === 0 ? (
        <div className="banner banner-empty">No rules defined yet — create one above first.</div>
      ) : (
        <>
          <ul className="apply-rules-list">
            {rules.map((rule) => (
              <li key={rule.rule_id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(rule.rule_id)}
                    onChange={() => toggle(rule.rule_id)}
                  />
                  {rule.name}{" "}
                  <span className="apply-rules-target">
                    ({rule.target_kind === "column_name" ? rule.target_value : `type: ${rule.target_value}`})
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <button type="button" onClick={handleApply} disabled={busy}>
            {busy ? "Applying…" : "Apply to this upload"}
          </button>
          {result && (
            <p className="apply-rules-result">
              {result.appliedCount} rule{result.appliedCount === 1 ? "" : "s"} now applied to this upload —
              check the Results tab for flagged cells.
            </p>
          )}
        </>
      )}
    </div>
  );
}
