import { Sun, Moon } from 'lucide-react';

// Custom BREACH mark — a shield torn by a breach crack. Inline so it inherits
// currentColor and stays crisp at any size.
function BreachMark({ className = '' }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" className={className} aria-hidden="true">
      <path
        d="M24 8 L37 12.5 V24 C37 32 31 37.5 24 40 C17 37.5 11 32 11 24 V12.5 Z"
        fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round"
      />
      <path
        d="M26 11 L20.5 23 L25 24.5 L21 37"
        fill="none" stroke="currentColor" strokeWidth="2.6"
        strokeLinecap="round" strokeLinejoin="round" opacity="0.85"
      />
    </svg>
  );
}

export default function Navbar({ connectionStatus, theme, setTheme }) {
  const statusMeta = {
    connected:    { dot: 'bg-emerald-500', label: 'System Online' },
    checking:     { dot: 'bg-amber-500 animate-pulse', label: 'Connecting' },
    disconnected: { dot: 'bg-rose-500', label: 'Offline' },
  }[connectionStatus] || { dot: 'bg-rose-500', label: 'Offline' };

  return (
    <header className="sticky top-0 z-50 w-full border-b border-claude-border bg-claude-bg/80 backdrop-blur-md transition-colors duration-300">
      <div className="mx-auto flex max-w-7xl h-16 items-center justify-between px-4 sm:px-6 lg:px-8">

        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-claude-border-strong bg-claude-card text-claude-accent">
            <BreachMark className="h-6 w-6" />
          </div>
          <div className="leading-none">
            <h1 className="font-display text-[22px] font-bold tracking-[0.14em] text-claude-text-primary">
              BREACH
            </h1>
            <p className="breach-label mt-1 !text-[9px]">Contract Risk Auditor</p>
          </div>
        </div>

        {/* Status + theme */}
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-2 rounded-md border border-claude-border bg-claude-card px-3 py-1.5">
            <span className={`h-2 w-2 rounded-full ${statusMeta.dot}`} />
            <span className="breach-label hidden sm:inline !tracking-[0.14em]">{statusMeta.label}</span>
          </div>

          <button
            onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-claude-border bg-claude-card text-claude-text-secondary hover:text-claude-accent hover:border-claude-border-strong cursor-pointer transition-colors duration-200"
            title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
          >
            {theme === 'light' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
          </button>
        </div>

      </div>
    </header>
  );
}
