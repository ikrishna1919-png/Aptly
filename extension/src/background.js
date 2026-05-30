// MV3 service worker. Holds no long-lived state beyond chrome.storage; wakes
// on messages. Responsibilities:
//   * receive the token from the aptly.fyi connect page (externally_connectable)
//   * relay QA lookups from the content script to the backend (content scripts
//     can't read the token from storage as cleanly, and this keeps the token
//     out of the page's world)
//   * keep the toolbar badge in sync with the detected field count per tab
import { api, AuthError } from "./lib/api.js";
import { setToken, getToken } from "./lib/storage.js";

// Token handoff from the connect page on aptly.fyi.
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "APTLY_CONNECT" && typeof msg.token === "string") {
    if (sender.origin === "https://aptly.fyi") {
      setToken(msg.token).then(() => sendResponse({ ok: true }));
      return true;
    }
    sendResponse({ ok: false, error: "bad origin" });
  }
  return false;
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
  return false;
});
