import RuleManager from "../components/rules/RuleManager";

export default function RulesPage({ selectedUploadId, columns }) {
  if (!selectedUploadId) return null;
  return <RuleManager columns={columns} />;
}
