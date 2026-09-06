import { DefaultButton, type IButtonStyles } from "@fluentui/react";
import "@/lib/fluent-icons";

import { noticeGoMark } from "@/components/studio/tenders/pipeline";
import { BUTTON_STYLES, type Tx } from "@/components/studio/tenders/tenders-ui";
import type { TenderNotice } from "@/lib/tenders-api";

const COMPACT: IButtonStyles = {
  root: {
    minHeight: 32,
    height: 32,
    minWidth: 0,
    paddingLeft: 10,
    paddingRight: 10,
    cursor: "pointer",
  },
  label: { fontWeight: 600, fontSize: 12 },
};

export function NoticeGoButtons({
  row,
  tx,
  busy,
  size = "compact",
  onGo,
  onNogo,
}: {
  row: TenderNotice;
  tx: Tx;
  busy?: boolean;
  size?: "compact" | "regular";
  onGo: () => void;
  onNogo: () => void;
}) {
  const mark = noticeGoMark(row);
  const styles = size === "regular" ? BUTTON_STYLES : COMPACT;
  return (
    <div className="flex flex-nowrap items-center gap-1" data-testid="tenders-notice-go-buttons">
      <DefaultButton
        toggle
        checked={mark === "go"}
        text={tx("rowGo", "GO")}
        iconProps={{ iconName: "Accept" }}
        disabled={Boolean(busy)}
        data-testid="tenders-notice-go"
        onClick={onGo}
        styles={styles}
      />
      <DefaultButton
        toggle
        checked={mark === "nogo"}
        text={tx("rowNogo", "No-go")}
        iconProps={{ iconName: "Cancel" }}
        disabled={Boolean(busy)}
        data-testid="tenders-notice-nogo"
        onClick={onNogo}
        styles={styles}
      />
    </div>
  );
}
