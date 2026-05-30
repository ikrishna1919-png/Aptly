// MV3 service worker. Holds no long-lived state beyond chrome.storage; wakes
// on messages. Responsibilities:
//   * receive the token from the aptly.fyi connect page (externally_connectable)
//   * relay QA lookups from the content script to the backend (content scripts
//     can't read the token from storage as cleanly, and this keeps the token
//     out of the page's world)
//   * keep the toolbar badge in sync with the detected field count per tab
import { api, AuthError } from "./lib/api.js";
import { setToken, getToken } from "./lib/storage.js";

const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

// ArrayBuffer → base64 (chunked to avoid arg-count limits on large buffers).
// btoa is available in the service-worker global scope.
function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

// Pull the filename out of a Content-Disposition header (handles RFC 5987
// filename*=UTF-8'' too). Empty string if absent.
function filenameFromDisposition(cd) {
  if (!cd) return "";
  const m = /filename\*?=(?:UTF-8'')?["']?([^"';]+)/i.exec(cd);
  return m ? decodeURIComponent(m[1].trim()) : "";
}

// Token handoff from the connect page on aptly.fyi. Primary type is
// APTLY_CONNECT_TOKEN; the older APTLY_CONNECT is still accepted so a
// connect-page/extension version skew doesn't break sign-in.
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  const isConnect =
    msg && (msg.type === "APTLY_CONNECT_TOKEN" || msg.type === "APTLY_CONNECT");
  if (!isConnect || typeof msg.token !== "string") {
    sendResponse({ error: "Unknown message type" });
    return false;
  }
  // Only trust the marketing origin (defense-in-depth alongside
  // externally_connectable in the manifest).
  if (!sender.origin || !sender.origin.startsWith("https://aptly.fyi")) {
    sendResponse({ error: "Unauthorized origin" });
    return false;
  }
  setToken(msg.token).then(() => {
    // Wake the popup if it's open so it can advance to the signed-in state.
    // No receiver (popup closed) throws lastError — swallow it.
    chrome.runtime.sendMessage({ type: "TOKEN_RECEIVED" }).catch(() => {});
    sendResponse({ ok: true });
  });
  return true; // async sendResponse
});

// Content script → field count → badge.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GH_FIELDS" && sender.tab) {
    const count = msg.hasForm && msg.count > 0 ? String(msg.count) : "";
    chrome.action.setBadgeBackgroundColor({ color: "#1E6FE0" });
    chrome.action.setBadgeText({ tabId: sender.tab.id, text: count });
    return false;
  }
  if (msg.type === "QA_LOOKUP") {
    getToken().then(async (t) => {
      if (!t) return sendResponse({ answer: null });
      try {
        const res = await api.qaLookup(msg.question_text, msg.field_type);
        sendResponse(res);
      } catch (e) {
        sendResponse({ answer: null, error: e instanceof AuthError ? "auth" : String(e) });
      }
    });
    return true; // async
  }
  // Resume auto-attach: fetch the tailored DOCX with the bearer token (the
  // content script can't and shouldn't), and hand it back base64-encoded — the
  // binary can't survive the JSON-serialized messaging boundary otherwise.
  if (msg.type === "RESUME_FILE") {
    getToken().then(async (t) => {
      if (!t) return sendResponse({ error: "auth" });
      try {
        const res = await api.resumeFile(msg.runId);
        const buf = await res.arrayBuffer();
        sendResponse({
          base64: arrayBufferToBase64(buf),
          filename: filenameFromDisposition(res.headers.get("Content-Disposition")) || "Aptly_Resume.docx",
          mime: res.headers.get("Content-Type") || DOCX_MIME,
        });
      } catch (e) {
        sendResponse({ error: e instanceof AuthError ? "auth" : String(e) });
      }
    });
    return true; // async
  }
  return false;
});
