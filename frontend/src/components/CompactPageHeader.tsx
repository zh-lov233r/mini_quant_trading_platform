import type { ReactNode } from "react";

import styles from "./AppShell.module.css";

export default function CompactPageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHeader}>
      <div className={styles.pageHeaderRow}>
        <div className={styles.pageHeading}>
          <h1 className={styles.title}>{title}</h1>
          {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
        </div>
        <div className={styles.headerControls}>
          {actions ? <div className={styles.pageActions}>{actions}</div> : null}
        </div>
      </div>
    </header>
  );
}
