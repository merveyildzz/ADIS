import { useState } from "react";
import ApplyRulesPanel from "../components/rules/ApplyRulesPanel";
import RuleManager from "../components/rules/RuleManager";

export default function RulesPage({ selectedUploadId, columns, onRulesApplied }) {
  // Bumped whenever RuleManager creates/edits/deletes a rule, so
  // ApplyRulesPanel (a sibling, with its own already-fetched rule list)
  // picks up the change instead of showing a stale checkbox list.
  const [rulesVersion, setRulesVersion] = useState(0);

  if (!selectedUploadId) return null;
  return (
    <>
      <ApplyRulesPanel key={rulesVersion} uploadId={selectedUploadId} onApplied={onRulesApplied} />
      <RuleManager
        uploadId={selectedUploadId}
        columns={columns}
        onRulesChanged={() => setRulesVersion((v) => v + 1)}
      />
    </>
  );
}
