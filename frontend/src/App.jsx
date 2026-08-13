import { useState, useEffect, useRef } from 'react';
import Navbar from './components/Navbar';
import DocumentUpload from './components/DocumentUpload';
import DocumentList from './components/DocumentList';
import ChatInterface from './components/ChatInterface';
import AuditDashboard from './components/AuditDashboard';
import { Shield, Bot } from 'lucide-react';
import { apiFetch } from './session';


// Backend endpoint configuration. Standard dev is localhost:8000/api
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

if (import.meta.env.PROD && !import.meta.env.VITE_API_URL) {
  // A deployed build with no VITE_API_URL silently points at the visitor's own
  // localhost and will never reach the real backend - surface that loudly.
  console.error(
    'VITE_API_URL is not set in this production build. The app will try to reach ' +
    'http://localhost:8000/api, which will fail for every real visitor.'
  );
}

export default function App() {
  const [documents, setDocuments] = useState([]);
  const [selectedDocId, setSelectedDocId] = useState(null);
  const [connectionStatus, setConnectionStatus] = useState('checking');
  const fetchRequestIdRef = useRef(0);
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState('dashboard');
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'dark';
  });

  useEffect(() => {
    const root = window.document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  // 1. Monitor backend connection health
  useEffect(() => {
    const checkConnection = async () => {
      try {
        const response = await fetch(`${API_BASE_URL.replace('/api', '')}/health`);
        if (response.ok) {
          setConnectionStatus('connected');
        } else {
          setConnectionStatus('disconnected');
        }
      } catch {
        setConnectionStatus('disconnected');
      }
    };
    
    checkConnection();
    // Re-check connection every 15 seconds
    const interval = setInterval(checkConnection, 15000);
    return () => clearInterval(interval);
  }, []);

  // 2. Fetch the initial list of documents
  const fetchDocuments = async (isInitial = false) => {
    // Guards against an older, slower poll response landing after a newer one
    // and overwriting fresher state with stale data.
    const requestId = ++fetchRequestIdRef.current;
    try {
      const response = await apiFetch(`${API_BASE_URL}/documents/`);
      if (requestId !== fetchRequestIdRef.current) return;

      if (response.ok) {
        const data = await response.json();
        setDocuments(data);
        if (isInitial && data.length > 0) {
          setSelectedDocId(data[0].id);
        }
      } else {
        console.error(`Failed to fetch documents: HTTP ${response.status}`);
        setConnectionStatus('disconnected');
      }
    } catch (err) {
      if (requestId !== fetchRequestIdRef.current) return;
      console.error("Error fetching documents:", err);
      setConnectionStatus('disconnected');
    }
  };

  useEffect(() => {
    fetchDocuments(true);
  }, []);

  // 3. Smart background status polling
  // Starts polling only when a file is currently indexing, and stops automatically when finished.
  useEffect(() => {
    const hasProcessing = documents.some(doc => doc.status === 'processing');
    if (!hasProcessing) return;

    const interval = setInterval(() => {
      fetchDocuments();
    }, 3000);

    return () => clearInterval(interval);
  }, [documents]);

  const handleUploadSuccess = (newDoc) => {
    // Add newly uploaded file immediately to the top of the list
    setDocuments(prev => [newDoc, ...prev]);
    // Switch to the newly uploaded document
    setSelectedDocId(newDoc.id);
  };

  const handleDeleteDocument = (id) => {
    // Remove the deleted document from local state
    setDocuments(prev => {
      const updated = prev.filter(doc => doc.id !== id);
      if (selectedDocId === id) {
        setSelectedDocId(updated.length > 0 ? updated[0].id : null);
      }
      return updated;
    });
  };

  return (
    <div className="relative min-h-screen lg:h-screen bg-claude-bg text-claude-text-primary overflow-hidden flex flex-col font-sans">

      {/* Forensic evidence-grid backdrop + a single restrained accent wash near the header */}
      <div className="absolute inset-0 breach-grid pointer-events-none" />
      <div className="absolute inset-x-0 top-0 h-72 breach-accent-wash pointer-events-none" />

      {/* Main Brand Header */}
      <Navbar connectionStatus={connectionStatus} theme={theme} setTheme={setTheme} />

      {/* Grid Dashboard Layout */}
      <main className="flex-1 mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8 py-6 z-10 flex flex-col lg:flex-row gap-6 overflow-hidden lg:h-[calc(100vh-4rem)]">
        
        {/* Sidebar Panel: Handles Document upload and listing */}
        <div className="w-full lg:w-80 flex flex-col gap-6 shrink-0 lg:h-full lg:overflow-hidden">
          <DocumentUpload 
            apiBaseUrl={API_BASE_URL} 
            onUploadSuccess={handleUploadSuccess} 
            documents={documents}
          />
          <DocumentList 
            documents={documents}
            selectedDocId={selectedDocId}
            onSelectDocument={setSelectedDocId}
            onDeleteDocument={handleDeleteDocument}
            apiBaseUrl={API_BASE_URL}
          />
        </div>

        {/* Workspace: Unified single large box with tab selector at the top */}
        <div className="flex-1 flex flex-col min-w-0 lg:h-full overflow-hidden glass-panel transition-colors duration-300">
          
          {/* Workspace Tabs */}
          <div className="flex bg-claude-sidebar/40 border-b border-claude-border shrink-0 transition-colors duration-300">
            <button
              onClick={() => setActiveWorkspaceTab('dashboard')}
              className={`relative flex-1 py-3.5 text-[11px] font-semibold font-mono uppercase tracking-[0.14em] cursor-pointer transition-colors duration-200 flex items-center justify-center gap-2 outline-none ${
                activeWorkspaceTab === 'dashboard'
                  ? 'text-claude-accent'
                  : 'text-claude-text-secondary hover:text-claude-text-primary'
              }`}
            >
              <Shield className="h-4 w-4" />
              <span>Risk Audit</span>
              {activeWorkspaceTab === 'dashboard' && (
                <span className="absolute inset-x-0 -bottom-px h-0.5 bg-claude-accent" />
              )}
            </button>
            <button
              onClick={() => setActiveWorkspaceTab('chat')}
              className={`relative flex-1 py-3.5 text-[11px] font-semibold font-mono uppercase tracking-[0.14em] cursor-pointer transition-colors duration-200 flex items-center justify-center gap-2 outline-none ${
                activeWorkspaceTab === 'chat'
                  ? 'text-claude-accent'
                  : 'text-claude-text-secondary hover:text-claude-text-primary'
              }`}
            >
              <Bot className="h-4 w-4" />
              <span>Ask Sara</span>
              {activeWorkspaceTab === 'chat' && (
                <span className="absolute inset-x-0 -bottom-px h-0.5 bg-claude-accent" />
              )}
            </button>
          </div>

          {/* Workspace Content Body */}
          <div className="flex-1 min-h-0 overflow-hidden relative">
            {activeWorkspaceTab === 'dashboard' ? (
              selectedDocId ? (
                <AuditDashboard 
                  document={documents.find(d => d.id === selectedDocId)}
                  apiBaseUrl={API_BASE_URL}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center text-claude-text-secondary p-8 transition-colors duration-300">
                  <Shield className="h-12 w-12 text-claude-border-strong mb-4 stroke-[1.5]" />
                  <p className="breach-label mb-2">No case loaded</p>
                  <h3 className="font-display font-bold text-claude-text-primary text-base mb-1.5">Nothing to audit yet</h3>
                  <p className="text-xs max-w-xs leading-relaxed">Select a contract from the docket to expose its risk profile.</p>
                </div>
              )
            ) : (
              <ChatInterface 
                apiBaseUrl={API_BASE_URL}
                selectedDocId={selectedDocId}
                documents={documents}
              />
            )}
          </div>
        </div>

      </main>
    </div>
  );
}
