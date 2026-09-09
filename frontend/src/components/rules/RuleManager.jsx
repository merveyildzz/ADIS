import { useEffect, useState } from "react";
import { createRule, deleteRule, listRules, updateRule } from "../../api";

const DETECTED_TYPES = [
  "email", "phone", "date", "currency", "quantity", "address", "numeric_age", "categorical",
];

const OPERATORS = [
  { value: "gte", label: ">= (number)" },
  { value: "lte", label: "<= (number)" },
  { value: "eq", label: "== (exact match)" },
  { value: "in", label: "one of (comma-separated list)" },
  { value: "regex_match", label: "matches regex" },
  { value: "not_null", label: "must not be empty" },
];

const NUMERIC_OPERATORS = new Set(["gte", "lte"]);

const EMPTY_DRAFT = {
  name: "",
  target_kind: "column_name",
  target_value: "",
  condition_operator: "not_null",
  condition_value: "",
  action: "flag",
  severity: "medium",
};

function toPayload(draft) {
  let condition_value = null;
  if (draft.condition_operator === "not_null") {
    condition_value = null;
  } else if (draft.condition_operator === "in") {
    condition_value = draft.condition_value.split(",").map((s) => s.trim()).filter(Boolean);
  } else if (NUMERIC_OPERATORS.has(draft.condition_operator)) {
    condition_value = Number(draft.condition_value);
  } else {
    condition_value = draft.condition_value;
  }
  return { ...draft, condition_value };
}

export default function RuleManager({ columns }) {
  const [rules, setRules] = useState([]);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState(null);
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(null);

  function refresh() {
    listRules()
      .then(setRules)
      .catch((err) => setLoadError(err.message));
  }

  useEffect(refresh, []);

  function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setErrors([]);
    const payload = toPayload(draft);
    const call = editingId ? updateRule(editingId, payload) : createRule(payload);
    call
      .then(() => {
        setDraft(EMPTY_DRAFT);
        setEditingId(null);
        refresh();
      })
      .catch((err) => {
        // Validation errors arrive as a JSON array of strings in `detail`;
        // request() stringifies it into err.message either way.
        setErrors(err.message.split(",").map((s) => s.trim()).filter(Boolean));
      })
      .finally(() => setBusy(false));
  }

  function startEdit(rule) {
    setEditingId(rule.rule_id);
    setDraft({
      name: rule.name,
      target_kind: rule.target_kind,
      target_value: rule.target_value,
      condition_operator: rule.condition_operator,
      condition_value: Array.isArray(rule.condition_value)
        ? rule.condition_value.join(", ")
        : rule.condition_value ?? "",
      action: rule.action,
      severity: rule.severity,
    });
    setErrors([]);
  }

  function handleDelete(ruleId) {
    deleteRule(ruleId)
      .then(refresh)
      .catch((err) => setLoadError(err.message));
  }

  return (
    <div className="rule-manager">
      <h2>Custom Rules</h2>
      <p className="rule-manager-hint">
        Rules run after cleaning, against the already-cleaned values. A rule can target a
        specific column, or a detected type (applies across any dataset with a column of
        that kind).
      </p>

      {loadError && <div className="banner banner-error">{loadError}</div>}

      <form className="rule-form" onSubmit={handleSubmit}>
        <label>
          Rule name
          <input
            type="text"
            required
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>

        <label>
          Target
          <select
            value={draft.target_kind}
            onChange={(e) => setDraft({ ...draft, target_kind: e.target.value, target_value: "" })}
          >
            <option value="column_name">A specific column</option>
            <option value="detected_type">Any column of a detected type</option>
          </select>
        </label>

        {draft.target_kind === "column_name" ? (
          <label>
            Column
            {columns?.length > 0 ? (
              <select
                required
                value={draft.target_value}
                onChange={(e) => setDraft({ ...draft, target_value: e.target.value })}
              >
                <option value="">Select a column…</option>
                {columns.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                required
                placeholder="column name"
                value={draft.target_value}
                onChange={(e) => setDraft({ ...draft, target_value: e.target.value })}
              />
            )}
          </label>
        ) : (
          <label>
            Detected type
            <select
              required
              value={draft.target_value}
              onChange={(e) => setDraft({ ...draft, target_value: e.target.value })}
            >
              <option value="">Select a type…</option>
              {DETECTED_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
        )}

        <label>
          Condition
          <select
            value={draft.condition_operator}
            onChange={(e) => setDraft({ ...draft, condition_operator: e.target.value, condition_value: "" })}
          >
            {OPERATORS.map((op) => (
              <option key={op.value} value={op.value}>{op.label}</option>
            ))}
          </select>
        </label>

        {draft.condition_operator !== "not_null" && (
          <label>
            Value
            <input
              type={NUMERIC_OPERATORS.has(draft.condition_operator) ? "number" : "text"}
              required
              placeholder={draft.condition_operator === "in" ? "active, inactive, pending" : ""}
              value={draft.condition_value}
              onChange={(e) => setDraft({ ...draft, condition_value: e.target.value })}
            />
          </label>
        )}

        <label>
          Severity
          <select value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>

        {errors.length > 0 && (
          <div className="banner banner-error">
            <ul>{errors.map((err, i) => <li key={i}>{err}</li>)}</ul>
          </div>
        )}

        <div className="rule-form-actions">
          <button type="submit" disabled={busy}>
            {editingId ? "Save changes" : "Add rule"}
          </button>
          {editingId && (
            <button type="button" onClick={() => { setEditingId(null); setDraft(EMPTY_DRAFT); setErrors([]); }}>
              Cancel
            </button>
          )}
        </div>
      </form>

      <table className="rule-list-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Target</th>
            <th>Condition</th>
            <th>Severity</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.rule_id}>
              <td>{rule.name}</td>
              <td>{rule.target_kind === "column_name" ? rule.target_value : `type: ${rule.target_value}`}</td>
              <td>
                {rule.condition_operator}
                {rule.condition_value !== null && ` ${JSON.stringify(rule.condition_value)}`}
              </td>
              <td>{rule.severity}</td>
              <td>
                <button type="button" onClick={() => startEdit(rule)}>Edit</button>
                <button type="button" onClick={() => handleDelete(rule.rule_id)}>Delete</button>
              </td>
            </tr>
          ))}
          {rules.length === 0 && (
            <tr>
              <td colSpan={5} className="banner-empty">No rules defined yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
