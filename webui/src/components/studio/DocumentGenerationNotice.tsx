import { MessageBar, MessageBarType } from "@fluentui/react";
import { useTranslation } from "react-i18next";

import type { DocumentGeneration } from "@/lib/document-generation";

export function DocumentGenerationNotice({ generation }: { generation?: DocumentGeneration }) {
  const { t } = useTranslation();
  if (!generation) return null;
  const warnings = generation.warnings?.filter(Boolean) || [];
  const model = generation.mode === "model";
  return (
    <MessageBar messageBarType={warnings.length ? MessageBarType.warning : MessageBarType.info} isMultiline>
      <strong>{t(`studio.generation.${model ? "modelTitle" : "structuredTitle"}`)}</strong>
      <p style={{ margin: "4px 0" }}>{t(`studio.generation.${model ? "modelBody" : "structuredBody"}`)}</p>
      {warnings.length ? (
        <details>
          <summary style={{ cursor: "pointer" }}>{t("studio.generation.reviewPoints")}</summary>
          <ul style={{ margin: "8px 0 0", paddingInlineStart: 20 }}>
            {warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
          </ul>
        </details>
      ) : null}
    </MessageBar>
  );
}
