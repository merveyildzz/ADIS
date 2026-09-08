import { useEffect, useRef, useState } from "react";
import { getConfig } from "../api";

export default function UploadZone({ busy, onFile }) {
  const [config, setConfig] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [clientError, setClientError] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch(() => setConfig(null)); // constraints text is a nicety, not required to function
  }, []);

  function validate(file) {
    if (!config) return null;
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!config.allowed_file_extensions.includes(ext)) {
      return `"${ext}" isn't an allowed file type. Allowed: ${config.allowed_file_extensions.join(", ")}`;
    }
    const maxBytes = config.max_upload_size_mb * 1024 * 1024;
    if (file.size > maxBytes) {
      return `File is ${(file.size / (1024 * 1024)).toFixed(1)} MB, which exceeds the ${config.max_upload_size_mb} MB limit.`;
    }
    return null;
  }

  function handleFile(file) {
    if (!file) return;
    const problem = validate(file);
    if (problem) {
      setClientError(problem);
      return;
    }
    setClientError(null);
    onFile(file);
  }

  return (
    <div className="upload-zone-wrap">
      <div
        className={`upload-zone ${dragging ? "upload-zone-dragging" : ""} ${busy ? "upload-zone-busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (busy) return;
          const file = e.dataTransfer.files?.[0];
          handleFile(file);
        }}
        onClick={() => !busy && inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept={config ? config.allowed_file_extensions.join(",") : ".csv"}
          disabled={busy}
          onChange={(e) => {
            handleFile(e.target.files?.[0]);
            e.target.value = "";
          }}
          hidden
        />
        <span className="upload-zone-label">
          {busy ? "Uploading…" : "Drop a CSV here, or click to choose a file"}
        </span>
        {config && (
          <span className="upload-zone-constraints">
            Max {config.max_upload_size_mb} MB · {config.allowed_file_extensions.join(", ")} only
          </span>
        )}
      </div>
      {clientError && <div className="banner banner-error">{clientError}</div>}
    </div>
  );
}
