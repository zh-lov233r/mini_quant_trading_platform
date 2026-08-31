import * as Dialog from "radix-ui/dialog";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import styles from "./WorkspaceDialog.module.css";

export interface WorkspaceDialogProps {
  triggerLabel?: string;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "summary" | "form";
  triggerTone?: "default" | "primary";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function WorkspaceDialog({
  triggerLabel,
  title,
  description,
  children,
  footer,
  size = "summary",
  triggerTone = "default",
  open: controlledOpen,
  onOpenChange,
}: WorkspaceDialogProps) {
  const router = useRouter();
  const [internalOpen, setInternalOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const previousOpenRef = useRef(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (next: boolean) => {
    if (controlledOpen == null) setInternalOpen(next);
    onOpenChange?.(next);
  };

  useEffect(() => {
    setOpen(false);
    // Closing on navigation is intentional; callers keep form values in page state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.asPath]);

  useEffect(() => {
    const wasOpen = previousOpenRef.current;
    previousOpenRef.current = open;
    if (open && !wasOpen) {
      if (document.activeElement instanceof HTMLElement) returnFocusRef.current = document.activeElement;
      const frame = window.requestAnimationFrame(() => closeRef.current?.focus());
      return () => window.cancelAnimationFrame(frame);
    }
    if (!open && wasOpen) {
      const target = triggerRef.current || returnFocusRef.current;
      if (target?.isConnected) target.focus();
    }
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      {triggerLabel ? (
        <Dialog.Trigger asChild>
          <button ref={triggerRef} type="button" className={`${styles.trigger} ${triggerTone === "primary" ? styles.triggerPrimary : ""}`}>
            {triggerLabel}
          </button>
        </Dialog.Trigger>
      ) : null}
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={`${styles.content} ${size === "form" ? styles.form : styles.summary}`}>
          <header className={styles.header}>
            <div>
              <Dialog.Title className={styles.dialogTitle}>{title}</Dialog.Title>
              {description ? <Dialog.Description className={styles.description}>{description}</Dialog.Description> : null}
            </div>
            <Dialog.Close asChild>
              <button ref={closeRef} type="button" className={styles.close} aria-label="Close">×</button>
            </Dialog.Close>
          </header>
          <div className={styles.body}>{children}</div>
          {footer ? <footer className={styles.footer}>{footer}</footer> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function DialogStack({ children }: { children: ReactNode }) {
  return <div className={styles.stack}>{children}</div>;
}

export function DialogGroup({ title, children }: { title: string; children: ReactNode }) {
  return <section className={styles.group}><h3 className={styles.groupTitle}>{title}</h3>{children}</section>;
}

export function DialogStats({ children }: { children: ReactNode }) {
  return <div className={styles.stats}>{children}</div>;
}

export function DialogStat({ label, value }: { label: string; value: ReactNode }) {
  return <div className={styles.stat}><span className={styles.label}>{label}</span><span className={styles.value}>{value}</span></div>;
}

export function DialogNote({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warning" }) {
  return <p className={`${styles.note} ${tone === "warning" ? styles.warning : ""}`}>{children}</p>;
}

export function DialogLinks({ children }: { children: ReactNode }) {
  return <div className={styles.links}>{children}</div>;
}

export function DialogLink({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className={styles.link}><span>{children}</span><span aria-hidden="true">→</span></Link>;
}
