import { forwardRef } from "react";
import type { SelectHTMLAttributes } from "react";

import styles from "./SelectControl.module.css";

export interface SelectControlProps extends SelectHTMLAttributes<HTMLSelectElement> {
  density?: "default" | "compact";
  invalid?: boolean;
}

export const SelectControl = forwardRef<HTMLSelectElement, SelectControlProps>(function SelectControl(
  { density = "default", invalid = false, className, children, ...props },
  ref,
) {
  return (
    <span className={`${styles.root} ${density === "compact" ? styles.compact : ""}`}>
      <select
        {...props}
        ref={ref}
        className={`${styles.select} ${className || ""}`}
        aria-invalid={invalid || undefined}
      >
        {children}
      </select>
      <svg className={styles.chevron} viewBox="0 0 20 20" aria-hidden="true">
        <path d="m6 8 4 4 4-4" />
      </svg>
    </span>
  );
});
