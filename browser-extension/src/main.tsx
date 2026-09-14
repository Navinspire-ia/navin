import { createRoot } from "react-dom/client";
import { initializeIcons } from "@fluentui/font-icons-mdl2";
import { App } from "./App";

// Bundle icon fonts locally so controls remain readable offline.
initializeIcons("./");

createRoot(document.getElementById("root")!).render(<App />);
