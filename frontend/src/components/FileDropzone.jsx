import { useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';

// Drag-and-drop area plus a Browse button.  Selection only: validation and
// upload are handled by the page that owns the file list.
export function FileDropzone({ onFiles, disabled = false, extensions = [], maxMb, maxFiles }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  const handle = (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length) onFiles(files);
  };

  return (
    <div
      className={`dropzone ${dragging ? 'dropzone-active' : ''} ${disabled ? 'dropzone-disabled' : ''}`}
      data-testid="dropzone"
      onDragEnter={(event) => {
        event.preventDefault();
        depth.current += 1;
        if (!disabled) setDragging(true);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        depth.current = 0;
        setDragging(false);
        if (!disabled) handle(event.dataTransfer?.files);
      }}
    >
      <UploadCloud size={36} aria-hidden="true" />
      <p className="dropzone-title">Drag and drop files here</p>
      <p className="muted">or</p>
      <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()} disabled={disabled}>
        Browse files
      </button>
      <input
        ref={inputRef}
        id="file-input"
        type="file"
        multiple
        className="sr-only"
        aria-label="Choose files to upload"
        accept={extensions.map((ext) => `.${ext}`).join(',')}
        disabled={disabled}
        onChange={(event) => {
          handle(event.target.files);
          event.target.value = ''; // allow choosing the same file again
        }}
      />
      <p className="dropzone-hint">
        Supported: {extensions.map((ext) => ext.toUpperCase()).join(', ')}
        <br />
        Up to {maxMb} MB per file, {maxFiles} files per upload.
      </p>
    </div>
  );
}
