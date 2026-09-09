/** Keep direct desk actions in the same project as their chat. */
export function studioSessionQuery(hash = typeof window === "undefined" ? "" : window.location.hash): string {
  const question = hash.indexOf("?");
  if (question < 0) return "";
  const key = new URLSearchParams(hash.slice(question + 1)).get("chat")?.trim() || "";
  return key.startsWith("websocket:") && key.length > "websocket:".length
    ? `&session_key=${encodeURIComponent(key)}`
    : "";
}
