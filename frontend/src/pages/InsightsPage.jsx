import InsightsView from "../components/InsightsView";

export default function InsightsPage({ selectedUploadId, onSelectRecord }) {
  if (!selectedUploadId) return null;
  return <InsightsView uploadId={selectedUploadId} onSelectRecord={onSelectRecord} />;
}
