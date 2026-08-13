import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Loader2, BookOpen, X, Trash2 } from 'lucide-react';
import { apiFetch, withSid } from '../session';

export default function ChatInterface({ apiBaseUrl, selectedDocId, documents }) {
  const [chats, setChats] = useState({});
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedSourceText, setSelectedSourceText] = useState(null);
  const [activeTab, setActiveTab] = useState('pdf');
  
  const messagesEndRef = useRef(null);

  const currentKey = selectedDocId || "global";

  // Derive current messages or default to the welcome message
  const messages = chats[currentKey] || [
    {
      sender: 'bot',
      text: selectedDocId 
        ? "Hi, I am Sara. I've loaded this document context. Ask me anything about it!"
        : "Hi, I am Sara, your AI legal assistant. Please upload a contract PDF to begin the automated legal risk audit.",
      sources: []
    }
  ];

  // Helper to update messages for the active key
  const setMessages = (updater) => {
    setChats(prev => {
      const currentMessages = prev[currentKey] || [
        {
          sender: 'bot',
          text: selectedDocId 
            ? "Hi, I am Sara. I've loaded this document context. Ask me anything about it!"
            : "Hi, I am Sara, your AI legal assistant. Please upload a contract PDF to begin the automated legal risk audit.",
          sources: []
        }
      ];
      const newMessages = typeof updater === 'function' ? updater(currentMessages) : updater;
      return {
        ...prev,
        [currentKey]: newMessages
      };
    });
  };

  // Auto scroll to bottom when messages update
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  // Close the citation sidebar when switching documents
  useEffect(() => {
    setSelectedSourceText(null);
  }, [selectedDocId]);

  const handleClearChat = () => {
    setChats(prev => ({
      ...prev,
      [currentKey]: [
        {
          sender: 'bot',
          text: selectedDocId 
            ? "Hi, I am Sara. I've loaded this document context. Ask me anything about it!"
            : "Hi, I am Sara, your AI legal assistant. Please upload a contract PDF to begin the automated legal risk audit.",
          sources: []
        }
      ]
    }));
    setSelectedSourceText(null);
  };

  const handleSend = async (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userQuestion = input.trim();
    setInput("");
    setLoading(true);
    setSelectedSourceText(null);

    // Format current messages as history payload before local state is updated
    const historyPayload = messages
      .filter(msg => msg.text && !msg.error)
      .map(msg => ({
        sender: msg.sender,
        text: msg.text
      }));

    // 1. Add User message to chat
    setMessages(prev => [...prev, { sender: 'user', text: userQuestion }]);

    // 2. Add placeholder Bot message that we will stream text into
    setMessages(prev => [...prev, { sender: 'bot', text: "", sources: [], streaming: true }]);

    try {
      const response = await apiFetch(`${apiBaseUrl}/query/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: userQuestion,
          document_id: selectedDocId,
          history: historyPayload
        })
      });

      if (!response.ok) {
        throw new Error("Failed to connect to AI engine.");
      }

      // 3. Extract source citations from custom HTTP header
      const sourcesHeader = response.headers.get("X-Sources");
      let sources = [];
      if (sourcesHeader) {
        try {
          sources = JSON.parse(sourcesHeader);
        } catch (e) {
          console.error("Error parsing sources header:", e);
        }
      }

      // 4. Read body stream chunk-by-chunk
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let done = false;
      let accumulatedText = "";

      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        
        if (value) {
          const chunk = decoder.decode(value, { stream: !done });
          accumulatedText += chunk;
          
          // Update the last message (which is our streaming bot message)
          setMessages(prev => {
            const updated = [...prev];
            const lastMsgIdx = updated.length - 1;
            if (lastMsgIdx >= 0 && updated[lastMsgIdx].sender === 'bot') {
              updated[lastMsgIdx] = {
                sender: 'bot',
                text: accumulatedText,
                sources: sources,
                streaming: !done
              };
            }
            return updated;
          });
        }
      }

    } catch (err) {
      console.error("Streaming error:", err);
      setMessages(prev => {
        const updated = [...prev];
        const lastMsgIdx = updated.length - 1;
        if (lastMsgIdx >= 0 && updated[lastMsgIdx].sender === 'bot') {
          updated[lastMsgIdx] = {
            sender: 'bot',
            text: `Sorry, I encountered an error: ${err.message}. Please check your Groq API key in the backend .env configuration.`,
            sources: [],
            error: true
          };
        }
        return updated;
      });
    } finally {
      setLoading(false);
    }
  };

  const getSelectedDocName = () => {
    if (!selectedDocId) return "No Document Selected";
    const doc = documents.find(d => d.id === selectedDocId);
    return doc ? (doc.title || doc.filename) : "Selected Document";
  };

  return (
    <div className="flex-1 flex flex-col md:flex-row gap-4 h-[calc(100vh-12rem)] lg:h-full lg:min-h-0 min-h-[480px]">
      
      {/* Chat Area */}
      <div className="flex-1 flex flex-col h-full overflow-hidden transition-colors duration-300">
        
        {/* Chat Header */}
        <div className="bg-claude-sidebar/60 border-b border-claude-border/80 px-4 py-3 flex items-center justify-between shrink-0 transition-colors duration-300">
          <div className="flex items-center space-x-2">
            <Bot className="h-5 w-5 text-claude-accent" />
            <div>
              <h3 className="text-sm font-semibold text-claude-text-primary">Sara</h3>
              <p className="text-[11px] text-claude-text-secondary">Querying: <span className="text-claude-accent font-medium">{getSelectedDocName()}</span></p>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            {loading && (
              <div className="flex items-center space-x-1.5 text-xs text-claude-accent">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span>Thinking...</span>
              </div>
            )}
            <button
              type="button"
              onClick={handleClearChat}
              disabled={loading || messages.length <= 1}
              className="flex items-center space-x-1 px-2.5 py-1.5 text-xs rounded-xl border border-claude-border hover:border-rose-500/20 bg-claude-sidebar/40 hover:bg-rose-500/5 text-claude-text-secondary hover:text-rose-500 cursor-pointer transition-all duration-200 disabled:opacity-30 disabled:pointer-events-none"
              title="Clear chat history"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Clear Chat</span>
            </button>
          </div>
        </div>

        {/* Message Log */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg, idx) => (
            <div 
              key={idx} 
              className={`flex items-start gap-3 max-w-[85%] ${
                msg.sender === 'user' ? 'ml-auto flex-row-reverse' : 'mr-auto'
              }`}
            >
              {/* Avatar */}
              <div className={`p-2 rounded-xl shrink-0 transition-colors duration-300 ${
                msg.sender === 'user' 
                  ? 'bg-claude-text-primary text-claude-bg' 
                  : msg.error
                    ? 'bg-rose-500/10 border border-rose-500/30 text-rose-500'
                    : 'bg-claude-accent text-white shadow-sm shadow-claude-accent/10'
              }`}>
                {msg.sender === 'user' ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
              </div>

              {/* Message Bubble */}
              <div className="space-y-2">
                <div className={`p-4 rounded-2xl text-sm leading-relaxed border transition-colors duration-300 ${
                  msg.sender === 'user' 
                    ? 'bg-claude-sidebar border-claude-border rounded-tr-none text-claude-text-primary shadow-sm' 
                    : msg.error
                      ? 'bg-rose-500/5 border-rose-500/20 rounded-tl-none text-rose-600 dark:text-rose-400 shadow-sm'
                      : 'bg-claude-card border-claude-border rounded-tl-none text-claude-text-primary shadow-sm'
                }`}>
                  {msg.text ? (
                    <div className="whitespace-pre-wrap markdown-content">{msg.text}</div>
                  ) : (
                    <div className="flex items-center space-x-2 text-claude-text-secondary py-1">
                      <span className="w-1.5 h-1.5 bg-claude-text-secondary rounded-full animate-bounce" />
                      <span className="w-1.5 h-1.5 bg-claude-text-secondary rounded-full animate-bounce [animation-delay:0.2s]" />
                      <span className="w-1.5 h-1.5 bg-claude-text-secondary rounded-full animate-bounce [animation-delay:0.4s]" />
                    </div>
                  )}
                </div>

                {/* Sources Row */}
                {msg.sources && msg.sources.length > 0 && (
                  <div className="flex flex-wrap gap-2 pl-1">
                    <span className="text-xs text-claude-text-secondary font-medium flex items-center space-x-1.5 mt-1 mr-1">
                      <BookOpen className="h-4 w-4" />
                      <span>Citations <span className="text-[11px] text-claude-accent font-normal italic">(click page to preview)</span>:</span>
                    </span>
                    {msg.sources.map((src, sIdx) => (
                      <button
                        key={sIdx}
                        onClick={() => {
                          setSelectedSourceText(src);
                          setActiveTab('pdf');
                        }}
                        className="text-[11px] bg-claude-sidebar hover:bg-claude-accent-bg border border-claude-border hover:border-claude-accent/30 text-claude-text-secondary hover:text-claude-accent px-2 py-0.5 rounded-md cursor-pointer transition-all duration-200"
                        title={`Click to read matching chunk. Similarity: ${(src.score * 100).toFixed(1)}%`}
                      >
                        Page {src.page_number} ({Math.round(src.score * 100)}%)
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Form */}
        <form onSubmit={handleSend} className="p-4 border-t border-claude-border/80 bg-claude-bg/60 shrink-0 transition-colors duration-300">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                documents.length === 0 
                  ? "Upload a document to begin..."
                  : "Ask a question about the active document..."
              }
              disabled={loading || documents.length === 0}
              className="flex-1 bg-claude-card border border-claude-border focus:border-claude-accent/60 rounded-xl px-4 py-3 text-sm text-claude-text-primary placeholder-claude-text-secondary/60 outline-none transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed focus:ring-1 focus:ring-claude-accent/25"
            />
            <button
              type="submit"
              disabled={loading || !input.trim() || documents.length === 0}
              className="bg-claude-accent text-white font-bold hover:bg-claude-accent-hover hover:shadow-md hover:shadow-claude-accent/10 p-3 rounded-xl transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer shrink-0"
            >
              {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
            </button>
          </div>
        </form>

      </div>

      {/* Citations Panel (Sidebar/Drawer) */}
      {selectedSourceText && (
        <div className="w-full lg:w-[450px] glass-panel flex flex-col h-full overflow-hidden shrink-0 border-claude-border transition-colors duration-300">
          {/* Panel Header */}
          <div className="bg-claude-sidebar/40 border-b border-claude-border px-4 py-3 flex items-center justify-between shrink-0 transition-colors duration-300">
            <div className="flex items-center space-x-2 text-claude-accent">
              <BookOpen className="h-4 w-4" />
              <span className="text-xs font-bold uppercase tracking-wider font-outfit">Source Inspection</span>
            </div>
            <button
              onClick={() => setSelectedSourceText(null)}
              className="text-claude-text-secondary hover:text-claude-text-primary p-1 rounded-lg transition-colors cursor-pointer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Panel Tabs */}
          <div className="flex bg-claude-bg border-b border-claude-border p-1 shrink-0 transition-colors duration-300">
            <button
              onClick={() => setActiveTab('pdf')}
              className={`flex-1 py-2 text-xs font-medium rounded-lg cursor-pointer transition-all duration-200 ${
                activeTab === 'pdf'
                  ? 'bg-claude-card text-claude-accent border border-claude-border shadow-sm'
                  : 'text-claude-text-secondary hover:text-claude-text-primary'
              }`}
            >
              Document Preview
            </button>
            <button
              onClick={() => setActiveTab('citation')}
              className={`flex-1 py-2 text-xs font-medium rounded-lg cursor-pointer transition-all duration-200 ${
                activeTab === 'citation'
                  ? 'bg-claude-card text-claude-accent border border-claude-border shadow-sm'
                  : 'text-claude-text-secondary hover:text-claude-text-primary'
              }`}
            >
              Extracted Text
            </button>
          </div>
          
          {/* Panel Content */}
          {activeTab === 'pdf' ? (
            <div className="flex-1 bg-claude-sidebar overflow-hidden relative transition-colors duration-300">
              <iframe
                key={`${selectedSourceText.document_id}-${selectedSourceText.page_number}`} // Force iframe reload when page/doc changes
                src={`${withSid(`${apiBaseUrl}/documents/${selectedSourceText.document_id}/pdf`)}#page=${selectedSourceText.page_number}`}
                className="w-full h-full border-0 bg-claude-sidebar"
                title="Document PDF Viewer"
                sandbox="allow-same-origin"
              />
            </div>
          ) : (
            <div className="p-4 flex-1 overflow-y-auto space-y-4">
              <div className="flex items-center justify-between text-xs font-mono text-claude-text-secondary">
                <span>Page Number: <strong className="text-claude-text-primary">{selectedSourceText.page_number}</strong></span>
                <span>Match: <strong className="text-claude-accent">{Math.round(selectedSourceText.score * 100)}%</strong></span>
              </div>
              
              {selectedSourceText.context && (
                <div className="bg-claude-accent/5 border border-claude-accent/20 rounded-xl p-3.5 space-y-1.5 shadow-sm transition-colors duration-300">
                  <span className="text-[10px] font-bold text-claude-accent uppercase tracking-wider font-mono block">Document Context (Situating Chunk)</span>
                  <p className="text-xs leading-relaxed text-claude-text-primary/90">{selectedSourceText.context}</p>
                </div>
              )}
              
              <div className="bg-claude-bg border border-claude-border rounded-xl p-4 text-xs leading-relaxed text-claude-text-primary/95 italic transition-colors duration-300">
                "{selectedSourceText.text}"
              </div>
              <p className="text-[10px] text-claude-text-secondary/80 text-center">This text block was retrieved from the PDF and injected into the LLM context prompt to generate the answer.</p>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
