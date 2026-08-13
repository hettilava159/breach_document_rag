const SESSION_STORAGE_KEY = 'breach_session_id';

// sessionStorage persists across a page refresh but is cleared when the tab
// closes - that's exactly the "keep on refresh, lose on close" behavior we
// want, for free, with no backend involvement.
export function getSessionId() {
  let sessionId = sessionStorage.getItem(SESSION_STORAGE_KEY);
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  }
  return sessionId;
}

// Drop-in replacement for fetch() that scopes the request to this browser
// tab's session so visitors never see each other's documents.
export function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-Session-Id', getSessionId());
  return fetch(url, { ...options, headers });
}

// For the handful of places that load a resource via a raw URL (iframe src,
// download links) instead of fetch() and so can't attach a custom header.
export function withSid(url) {
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}sid=${encodeURIComponent(getSessionId())}`;
}
