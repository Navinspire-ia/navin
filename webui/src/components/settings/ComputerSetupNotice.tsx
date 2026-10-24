// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { motion, useReducedMotion } from "framer-motion";
import { Eye, Monitor, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { computerSettingsUrl, type ComputerSetupIssue } from "@/lib/computer-setup";
import "./computer-settings.css";

export function ComputerSetupNotice({ issue }: { issue: ComputerSetupIssue }) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const IssueIcon = issue === "vision" ? Eye : ShieldAlert;

  return (
    <motion.aside
      className="computer-setup-notice"
      data-testid="computer-setup-notice"
      initial={false}
      animate={{ opacity: 1 }}
      transition={{ duration: reduced ? 0 : 0.18 }}
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <IssueIcon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <h4 className="m-0 text-sm font-semibold">{t(`settings.computer.noticeTitle.${issue}`)}</h4>
        </div>
        <p className="m-0 text-sm text-muted-foreground">{t(`settings.computer.noticeBody.${issue}`)}</p>
        <motion.div className="self-start" whileTap={reduced ? undefined : { scale: 0.96 }}>
          <Button asChild className="min-h-10">
            <a
              href={computerSettingsUrl(issue)}
              onClick={(event) => {
                event.preventDefault();
                window.location.hash = computerSettingsUrl(issue, window.location.hash);
              }}
            >
              <Monitor className="h-4 w-4" aria-hidden />
              {t("settings.computer.openSetup")}
            </a>
          </Button>
        </motion.div>
      </div>
    </motion.aside>
  );
}
