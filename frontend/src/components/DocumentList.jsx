import React from 'react';
import { Trash2, FileText, CheckCircle2, AlertCircle, Loader2, Sparkles } from 'lucide-react';
import { apiFetch } from '../session';

export default function DocumentList({
  documents,
  selectedDocId,
  onSelectDocument,
  onDeleteDocument,
  apiBaseUrl
}) {
  const [confirmDeleteId, setConfirmDeleteId] = React.useState(null);
  const [deleteError, setDeleteError] = React.useState(null);

  const formatBytes = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const formatDate = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const requestDelete = (e, id) => {
    e.stopPropagation(); // Prevent selecting the document when clicking delete
    setDeleteError(null);
    setConfirmDeleteId(id);
  };

  const cancelDelete = (e) => {
    e.stopPropagation();
    setConfirmDeleteId(null);
  };

  const confirmDelete = async (e, id) => {
    e.stopPropagation();
    setConfirmDeleteId(null);
    try {
      const response = await apiFetch(`${apiBaseUrl}/documents/${id}`, {
        method: "DELETE"
      });
      if (response.ok) {
        onDeleteDocument(id);
      } else {
        setDeleteError("Failed to delete document. Please try again.");
      }
    } catch (err) {
      console.error("Error deleting document:", err);
      setDeleteError("An error occurred while deleting the document.");
    }
  };

  return (
    <div className="glass-panel p-6 flex flex-col h-full lg:h-auto lg:flex-1 lg:min-h-0 transition-colors duration-300">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <h2 className="font-display text-lg font-bold text-claude-text-primary flex items-center space-x-2">
          <FileText className="h-5 w-5 text-claude-accent" />
          <span>Case Docket</span>
        </h2>
        <span className="breach-label bg-claude-sidebar border border-claude-border px-2 py-1 rounded-md transition-colors duration-300">
          {documents.length} {documents.length === 1 ? 'file' : 'files'}
        </span>
      </div>

      {deleteError && (
        <div className="mb-3 flex items-start space-x-2 p-2.5 rounded-lg bg-rose-500/5 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs shrink-0">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{deleteError}</span>
        </div>
      )}

      {/* Document List */}
      <div className="flex-1 overflow-y-auto space-y-2 lg:max-h-none pr-1">
        {documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 border border-dashed border-claude-border rounded-xl text-claude-text-secondary p-4 text-center transition-colors duration-300">
            <FileText className="h-10 w-10 text-claude-border mb-2 stroke-[1.5]" />
            <p className="text-sm font-medium">No documents uploaded</p>
            <p className="text-xs text-claude-text-secondary/80 mt-1">Upload a PDF to start asking questions.</p>
          </div>
        ) : (
          documents.map((doc) => {
            const isSelected = selectedDocId === doc.id;
            const isProcessing = doc.status === 'processing';
            const isError = doc.status === 'error';
            
            return (
              <div
                key={doc.id}
                onClick={() => !isProcessing && onSelectDocument(doc.id)}
                role="button"
                tabIndex={isProcessing ? -1 : 0}
                aria-disabled={isProcessing}
                aria-current={isSelected ? "true" : undefined}
                onKeyDown={(e) => {
                  if (!isProcessing && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    onSelectDocument(doc.id);
                  }
                }}
                className={`group flex items-center justify-between p-3 rounded-xl border transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-claude-accent ${
                  isProcessing
                    ? "opacity-60 cursor-not-allowed border-claude-border bg-claude-sidebar/10"
                    : isSelected
                      ? "border-claude-accent bg-claude-accent-bg text-claude-accent font-semibold"
                      : "border-claude-border bg-claude-sidebar/20 hover:border-claude-border hover:bg-claude-sidebar/40 cursor-pointer text-claude-text-primary"
                }`}
              >
                <div className="flex items-center space-x-3 min-w-0 flex-1">
                  <div className={`p-2 rounded-lg transition-colors duration-200 ${
                    isSelected ? 'bg-claude-accent/20 text-claude-accent' : 'bg-claude-card border border-claude-border text-claude-text-secondary'
                  }`}>
                    <FileText className="h-4.5 w-4.5" />
                  </div>
                  
                  <div className="min-w-0 flex-1">
                    <p 
                      className={`text-sm font-medium truncate pr-2 text-claude-text-primary`}
                      title={doc.title || doc.filename}
                    >
                      {doc.title || doc.filename}
                    </p>
                    {doc.author && (
                      <p className="text-[11px] text-claude-accent truncate pr-2 mt-0.5 font-medium" title={doc.author}>
                        By {doc.author}
                      </p>
                    )}
                    <div className="flex items-center space-x-2 text-[10px] text-claude-text-secondary mt-0.5">
                      {doc.title && <span className="truncate max-w-[80px] font-mono text-[9px] text-claude-text-secondary/70" title={doc.filename}>{doc.filename}</span>}
                      {doc.title && <span>•</span>}
                      <span>{formatBytes(doc.file_size)}</span>
                      <span>•</span>
                      <span>{formatDate(doc.uploaded_at)}</span>
                      <span>•</span>
                      {doc.has_context ? (
                        <span className="flex items-center text-purple-600 dark:text-purple-400 bg-purple-500/10 dark:bg-purple-500/20 px-1 rounded text-[9px] font-semibold font-mono tracking-tight" title="Uploaded with Contextual Retrieval">
                          <Sparkles className="h-2.5 w-2.5 mr-0.5 text-purple-500" /> Contextual
                        </span>
                      ) : (
                        <span className="flex items-center text-slate-500 dark:text-slate-400 bg-slate-500/10 dark:bg-slate-500/20 px-1 rounded text-[9px] font-medium font-mono tracking-tight" title="Uploaded without Contextual Retrieval">
                          Standard
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center space-x-2 shrink-0">
                  {/* Status Indicator */}
                  {isProcessing && (
                    <div className="flex items-center space-x-1 text-amber-600 dark:text-amber-400 px-2 py-0.5 rounded-full bg-amber-500/5 text-[10px] font-medium border border-amber-500/20">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>Analyzing {doc.processing_progress !== undefined && doc.processing_progress !== null ? `${doc.processing_progress}%` : '0%'}</span>
                    </div>
                  )}

                  {isError && (
                    <div className="flex items-center space-x-1 text-rose-600 dark:text-rose-400 px-2 py-0.5 rounded-full bg-rose-500/5 text-[10px] font-medium border border-rose-500/20">
                      <AlertCircle className="h-3 w-3" />
                      <span>Failed</span>
                    </div>
                  )}

                  {doc.status === 'processed' && (
                    <div className="flex items-center space-x-1">
                      {doc.has_audit && doc.audit_score !== undefined && doc.audit_score !== null ? (
                        <div className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                          doc.audit_score >= 85
                            ? 'bg-emerald-500/5 border-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                            : doc.audit_score >= 60
                              ? 'bg-amber-500/5 border-amber-500/20 text-amber-600 dark:text-amber-400'
                              : 'bg-rose-500/5 border-rose-500/20 text-rose-600 dark:text-rose-400'
                        }`}>
                          Score: {doc.audit_score}
                        </div>
                      ) : (
                        <div className="flex items-center space-x-1 text-emerald-600 dark:text-emerald-400 px-2 py-0.5 rounded-full bg-emerald-500/5 text-[10px] font-medium border border-emerald-500/20">
                          <CheckCircle2 className="h-3 w-3" />
                          <span>Ready</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Delete Button */}
                  {confirmDeleteId === doc.id ? (
                    <div className="flex items-center space-x-1">
                      <button
                        onClick={(e) => confirmDelete(e, doc.id)}
                        className="px-2 py-1 rounded-lg text-[10px] font-semibold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 hover:bg-rose-500/20 cursor-pointer transition-all duration-200"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={cancelDelete}
                        className="px-2 py-1 rounded-lg text-[10px] font-semibold text-claude-text-secondary border border-claude-border hover:bg-claude-sidebar/40 cursor-pointer transition-all duration-200"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => requestDelete(e, doc.id)}
                      className="p-1.5 rounded-lg text-claude-text-secondary hover:text-rose-500 hover:bg-rose-500/5 border border-transparent hover:border-rose-500/10 cursor-pointer transition-all duration-200"
                      title="Delete document"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
