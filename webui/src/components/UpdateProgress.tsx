// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from "react";
import { Customizer, ProgressIndicator, createTheme } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import type { UpdateStatus } from "@/lib/api";

export function UpdateProgress({ status }: { status: UpdateStatus | null }) {
  const { t } = useTranslation();
  const dark = useThemeValue() === "dark";
  const reduced = useReducedMotion();
  const theme = useMemo(() => createTheme({
    isInverted: dark,
    palette: dark ? {
      themePrimary: "#62abf5", white: "#17191d", black: "#f5f5f5",
      neutralPrimary: "#f5f5f5", neutralSecondary: "#c2c8d0",
    } : { themePrimary: "#0078d4" },
    defaultFontStyle: { fontFamily: "inherit" },
  }), [dark]);
  if (!status || status.state === "error") return null;
  const downloading = status.state === "downloading";
  const percent = Math.max(0, Math.min(100, Math.round(status.progress || 0)));
  const label = status.state === "restarting"
    ? t("updates.restarting", { defaultValue: "Restarting Navin..." })
    : downloading
      ? status.totalBytes > 0
        ? t("updates.downloading", { defaultValue: "Downloading {{percent}}%", percent })
        : t("updates.preparing", { defaultValue: "Preparing the download..." })
      : t("updates.installing", { defaultValue: "Installing..." });
  return <Customizer settings={{ theme }}>
    <motion.div initial={false} animate={{ opacity: 1 }}
      transition={{ duration: reduced ? 0 : 0.18 }} aria-live="polite">
      <ProgressIndicator label={label}
        percentComplete={downloading && status.totalBytes > 0 ? percent / 100 : undefined}
        styles={{
          root: { minWidth: 160, paddingTop: 8 },
          itemName: { fontSize: 12, fontVariantNumeric: "tabular-nums" },
          progressBar: reduced ? { transition: "none" } : undefined,
        }} />
    </motion.div>
  </Customizer>;
}
