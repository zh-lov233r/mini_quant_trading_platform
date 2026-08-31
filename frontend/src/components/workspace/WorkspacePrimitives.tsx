import type { CSSProperties, ReactNode } from "react";

import styles from "./WorkspacePrimitives.module.css";

function cx(...values: Array<string | false | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function KpiStrip({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx(styles.kpiStrip, className)}>{children}</section>;
}

export function WorkspaceGrid({ children, balanced = false, className, style }: { children: ReactNode; balanced?: boolean; className?: string; style?: CSSProperties }) {
  return <div className={cx(styles.workspaceGrid, balanced && styles.workspaceGridBalanced, className)} style={style}>{children}</div>;
}

export function DensePanel({ children, sticky = false, className, style }: { children: ReactNode; sticky?: boolean; className?: string; style?: CSSProperties }) {
  return <section className={cx(styles.densePanel, sticky && styles.sticky, className)} style={style}>{children}</section>;
}
