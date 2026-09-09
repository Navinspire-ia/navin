import { useMemo } from "react";
import { Customizer, Icon, PrimaryButton, Stack, Text, createTheme } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { computerSettingsUrl, type ComputerSetupIssue } from "@/lib/computer-setup";
import "@/lib/fluent-icons";
import "./computer-settings.css";

export function ComputerSetupNotice({ issue }: { issue: ComputerSetupIssue }) {
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

  return <Customizer settings={{ theme }}>
    <motion.aside className="computer-setup-notice" data-testid="computer-setup-notice"
      initial={false} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : 0.18 }}>
      <Stack tokens={{ childrenGap: 12 }}>
        <Stack horizontal verticalAlign="center" tokens={{ childrenGap: 8 }}>
          <Icon iconName={issue === "vision" ? "RedEye" : "Permissions"} aria-hidden />
          <Text as="h4" styles={{ root: { margin: 0, fontWeight: 600 } }}>{t(`settings.computer.noticeTitle.${issue}`)}</Text>
        </Stack>
        <Text block>{t(`settings.computer.noticeBody.${issue}`)}</Text>
        <motion.div whileTap={reduced ? undefined : { scale: 0.96 }}>
          <PrimaryButton text={t("settings.computer.openSetup")} iconProps={{ iconName: "TVMonitor" }}
            href={computerSettingsUrl(issue)} styles={{ root: { minHeight: 40 } }}
            onClick={(event) => { event.preventDefault(); window.location.hash = computerSettingsUrl(issue, window.location.hash); }} />
        </motion.div>
      </Stack>
    </motion.aside>
  </Customizer>;
}
