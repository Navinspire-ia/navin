// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import ReactDOM from "react-dom/client";

import "../globals.css";
import { GuestApp } from "./GuestApp";
import { loadGuestLocale } from "./guest-i18n";
import { applyGuestTheme, initialGuestTheme } from "./guest-theme";

// Day or night before the first paint of the app.
applyGuestTheme(initialGuestTheme());

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
void loadGuestLocale().finally(() => ReactDOM.createRoot(root).render(<GuestApp />));
