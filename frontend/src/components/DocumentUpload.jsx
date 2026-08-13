import { useState, useRef, useEffect } from 'react';
import { UploadCloud, AlertCircle, CheckCircle, FileText, Loader2 } from 'lucide-react';
import { apiFetch } from '../session';

export default function DocumentUpload({ apiBaseUrl, onUploadSuccess, documents = [] }) {
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const [uploadedDocId, setUploadedDocId] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [useContextualRetrieval, setUseContextualRetrieval] = useState(false);
  
  const fileInputRef = useRef(null);

  // Clear success notification once indexing completes (or the document is deleted
  // mid-processing, in which case it will never appear with a non-processing status).
  useEffect(() => {
    if (uploadedDocId) {
      const uploadedDoc = documents.find(doc => doc.id === uploadedDocId);
      if (!uploadedDoc || uploadedDoc.status !== 'processing') {
        setSuccess(false);
        setUploadedDocId(null);
      }
    }
  }, [documents, uploadedDocId]);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    // Mirror the disabled file-input guard so a drop mid-upload can't fire a
    // second concurrent upload that clobbers this component's shared state.
    if (loading) return;

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      await uploadFile(e.dataTransfer.files[0]);
    }
  };

  const handleChange = async (e) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      await uploadFile(e.target.files[0]);
    }
  };

  const triggerFileInput = () => {
    fileInputRef.current.click();
  };

  const uploadFile = async (file) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError("Only PDF files are supported.");
      return;
    }

    // Limit to 10MB to avoid local server crashes
    if (file.size > 10 * 1024 * 1024) {
      setError("File is too large. Maximum size is 10MB.");
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(false);
    setUploadedFile(file.name);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await apiFetch(`${apiBaseUrl}/documents/?generate_context=${useContextualRetrieval}`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to upload file.");
      }

      const result = await response.json();
      setUploadedDocId(result.id);
      setSuccess(true);
      
      // Notify parent app of new document
      if (onUploadSuccess) {
        onUploadSuccess(result);
      }
    } catch (err) {
      setError(err.message || "An error occurred during upload.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-panel p-6 lg:p-4 flex flex-col lg:h-[30%] lg:min-h-[220px] lg:shrink-0 lg:overflow-hidden transition-colors duration-300">
      <h2 className="font-display text-lg lg:text-base font-bold text-claude-text-primary mb-4 lg:mb-2.5 flex items-center space-x-2 shrink-0">
        <FileText className="h-5 w-5 lg:h-4 lg:w-4 text-claude-accent" />
        <span>Submit Evidence</span>
      </h2>

      {/* Toggle Contextual Retrieval */}
      <div className="flex items-center justify-between p-3.5 lg:p-2.5 mb-4 lg:mb-2.5 rounded-xl border border-claude-border bg-claude-sidebar/40 shrink-0 transition-colors duration-300">
        <div className="flex flex-col space-y-0.5">
          <span className="text-xs lg:text-[11px] font-semibold text-claude-text-primary">Deep Compliance Audit</span>
          <span className="text-[10px] lg:text-[9px] text-claude-text-secondary leading-tight">Runs Tavily Search & contextual analysis (more thorough)</span>
        </div>
        <button
          type="button"
          onClick={() => setUseContextualRetrieval(!useContextualRetrieval)}
          className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out outline-none ${
            useContextualRetrieval ? 'bg-claude-accent' : 'bg-claude-border'
          }`}
          aria-pressed={useContextualRetrieval}
        >
          <span
            className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-claude-card shadow-sm ring-0 transition duration-200 ease-in-out ${
              useContextualRetrieval ? 'translate-x-4' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {/* Drag & Drop Box */}
      <form
        onDragEnter={handleDrag}
        onDragOver={handleDrag}
        onDragLeave={handleDrag}
        onDrop={handleDrop}
        onSubmit={(e) => e.preventDefault()}
        className={`relative flex-1 min-h-0 flex flex-col items-center justify-center p-8 lg:p-3 border-2 border-dashed rounded-xl cursor-pointer transition-all duration-300 outline-none focus-visible:ring-2 focus-visible:ring-claude-accent ${
          dragActive
            ? "border-claude-accent bg-claude-accent-bg shadow-md shadow-claude-accent/5"
            : "border-claude-border hover:border-claude-accent bg-claude-sidebar/20 hover:bg-claude-sidebar/40"
        }`}
        onClick={triggerFileInput}
        role="button"
        tabIndex={0}
        aria-label="Upload PDF document"
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            triggerFileInput();
          }
        }}
      >
        <input 
          ref={fileInputRef}
          type="file" 
          className="hidden" 
          accept=".pdf" 
          onChange={handleChange}
          disabled={loading}
        />

        {loading ? (
          <div className="flex flex-col items-center text-center space-y-2 lg:space-y-1">
            <Loader2 className="h-10 w-10 lg:h-6 lg:w-6 text-claude-accent animate-spin" />
            <div>
              <p className="font-semibold text-claude-text-primary text-sm lg:text-xs">Uploading {uploadedFile}...</p>
              <p className="text-xs lg:text-[10px] text-claude-text-secondary mt-1 lg:mt-0.5">Parsing and analyzing...</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center text-center space-y-3 lg:space-y-1.5">
            <div className="p-3 lg:p-1.5 rounded-full bg-claude-card border border-claude-border text-claude-text-secondary shrink-0">
              <UploadCloud className="h-8 w-8 lg:h-5 lg:w-5 text-claude-accent" />
            </div>
            <div>
              <p className="font-semibold text-claude-text-primary text-sm lg:text-xs">
                Drag & drop PDF here, or <span className="text-claude-accent hover:underline font-medium">browse</span>
              </p>
              <p className="text-xs lg:text-[10px] text-claude-text-secondary mt-1 lg:mt-0.5">Max size 10MB</p>
            </div>
          </div>
        )}
      </form>

      {/* Status Messages */}
      {error && (
        <div className="mt-4 lg:mt-2.5 flex items-start space-x-2.5 p-3 lg:p-2 rounded-lg bg-rose-500/5 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-sm lg:text-xs shrink-0">
          <AlertCircle className="h-5 w-5 lg:h-4 lg:w-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {success && (
        <div className="mt-4 lg:mt-2.5 flex items-start space-x-2.5 p-3 lg:p-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-sm lg:text-xs shrink-0">
          <CheckCircle className="h-5 w-5 lg:h-4 lg:w-4 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Upload complete!</p>
          </div>
        </div>
      )}
    </div>
  );
}
