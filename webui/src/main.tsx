import ReactDOM from "react-dom/client";

import App from "./App";
import "./globals.css";
import "./i18n";
import { registerUsagePwa } from "./pwa";
import { installDesktopChromeGuard } from "./lib/desktop-chrome-guard";
import { installDisableNativeAutocorrect } from "./lib/disable-native-autocorrect";
import { initHostChromePreview } from "./lib/host-chrome";
import { initUiZoom } from "./lib/ui-zoom";

// `crypto.randomUUID` is only defined in secure contexts (HTTPS or localhost).
// LAN access over plain HTTP leaves it undefined, which crashes components that
// generate client-side message IDs. Shim a v4-ish fallback so call sites stay
// uniform across secure and non-secure contexts.
if (typeof globalThis.crypto !== "undefined" && !("randomUUID" in globalThis.crypto)) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    value: () =>
      "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === "x" ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      }),
    configurable: true,
  });
}

// Before the first paint, so a zoomed-in user never sees a frame at 100 %.
initUiZoom();
// Optional: ``#/code?hostChrome=1`` paints the Tauri chrome in the browser.
initHostChromePreview();
// macOS otherwise rewrites tokens in chat and model search (gpt -> got).
installDisableNativeAutocorrect();
// Desktop shell only: no browser context menu, reload keys, history buttons
// or drop-to-navigate. The IDE is not a browser tab.
installDesktopChromeGuard();

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");

/* StrictMode disabled: dev double-invokes state updaters; delta accumulation must stay pure - see useNavinStream. */
ReactDOM.createRoot(root).render(<App />);

registerUsagePwa();

