import * as Dialog from "radix-ui/dialog";
import * as Tooltip from "radix-ui/tooltip";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { useI18n } from "@/i18n/provider";

import styles from "./AppShell.module.css";
import CompactPageHeader from "./CompactPageHeader";
import {
  isWorkspaceRouteActive,
  parseStoredSidebarCollapsed,
  serializeSidebarCollapsed,
  SIDEBAR_STORAGE_KEY,
  WORKSPACE_NAV_ITEMS,
  type WorkspaceNavKey,
} from "./workspace/workspaceLayout";

interface AppShellProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  contentMode?: "workspace" | "wide" | "reading";
}

interface PageActionLinkProps {
  href: string;
  children: ReactNode;
  primary?: boolean;
}

function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function PageActionLink({
  href,
  children,
  primary = false,
}: PageActionLinkProps) {
  return (
    <Link
      href={href}
      className={cx(styles.pageActionLink, primary && styles.pageActionLinkPrimary)}
    >
      {children}
    </Link>
  );
}

export default function AppShell({
  title,
  subtitle,
  actions,
  children,
  contentMode = "workspace",
}: AppShellProps) {
  const router = useRouter();
  const { locale, setLocale, messages } = useI18n();
  const isZh = locale === "zh-CN";
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    const stored = parseStoredSidebarCollapsed(window.localStorage.getItem(SIDEBAR_STORAGE_KEY));
    if (stored != null) setSidebarCollapsed(stored);
  }, []);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [router.asPath]);

  useEffect(() => {
    window.dispatchEvent(new Event("workspace-layout-change"));
  }, [sidebarCollapsed]);

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, serializeSidebarCollapsed(next));
      return next;
    });
  };

  const navLabels: Record<WorkspaceNavKey, string> = {
    dashboard: messages.nav.dashboard,
    strategies: messages.nav.strategies,
    stockBaskets: messages.nav.stockBaskets,
    backtests: messages.nav.backtests,
    backtestTasks: messages.nav.backtestTasks,
    research: messages.nav.research,
    paperTrading: messages.nav.paperTrading,
  };

  const navContent = (mobile = false) => (
    <nav className={mobile ? styles.mobileNav : styles.primaryNav} aria-label={isZh ? "主导航" : "Primary navigation"}>
      {WORKSPACE_NAV_ITEMS.map((item) => {
        const active = isWorkspaceRouteActive(router.pathname, item.href);
        const label = navLabels[item.key];
        return (
          <Tooltip.Root key={item.href} delayDuration={350}>
            <Tooltip.Trigger asChild>
              <Link href={item.href} aria-current={active ? "page" : undefined} className={cx(styles.navLink, active && styles.navLinkActive)} onClick={() => mobile && setMobileNavOpen(false)}>
                <NavIcon navKey={item.key} className={styles.navIcon} />
                <span className={styles.navLabel}>{label}</span>
              </Link>
            </Tooltip.Trigger>
            {!mobile ? <Tooltip.Portal><Tooltip.Content side="right" sideOffset={9} className="workspace-tooltip">{label}</Tooltip.Content></Tooltip.Portal> : null}
          </Tooltip.Root>
        );
      })}
      <Link href="/strategies/new" className={styles.quickCreate} onClick={() => mobile && setMobileNavOpen(false)}>
        <span aria-hidden="true" style={{ fontSize: 20, lineHeight: 1 }}>+</span>
        <span className={cx(styles.quickCreateLabel, styles.navLabel)}>{messages.nav.newStrategy}</span>
      </Link>
    </nav>
  );

  return (
    <Tooltip.Provider>
      <div className={cx(styles.shell, sidebarCollapsed && styles.shellCollapsed)} data-workspace-sidebar-collapsed={sidebarCollapsed}>
        <aside className={styles.sidebar}>
          <div className={styles.brandRow}>
            <Link href="/" className={styles.brand}>{messages.common.appName}</Link>
            <Tooltip.Root>
              <Tooltip.Trigger asChild>
                <button type="button" className={cx(styles.iconButton, styles.collapseDesktop)} onClick={toggleSidebar} aria-label={sidebarCollapsed ? (isZh ? "展开导航" : "Expand navigation") : (isZh ? "收起导航" : "Collapse navigation")}>
                  <ChevronIcon direction={sidebarCollapsed ? "right" : "left"} />
                </button>
              </Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content side="right" sideOffset={9} className="workspace-tooltip">{sidebarCollapsed ? (isZh ? "展开导航" : "Expand navigation") : (isZh ? "收起导航" : "Collapse navigation")}</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>
          </div>
          {navContent()}
          <div className={styles.sidebarSpacer} />
          <div className={styles.sidebarFooter}>
            <div className={styles.localeSwitch} aria-label={messages.common.language}>
              <button type="button" className={cx(styles.localeButton, styles.localeChoiceButton, locale === "zh-CN" && styles.localeButtonActive)} onClick={() => setLocale("zh-CN")}>中<span className={styles.localeLongLabel}>文</span></button>
              <button type="button" className={cx(styles.localeButton, styles.localeChoiceButton, locale === "en-US" && styles.localeButtonActive)} onClick={() => setLocale("en-US")}>EN</button>
              <button
                type="button"
                className={cx(styles.localeButton, styles.localeButtonActive, styles.localeToggleButton)}
                onClick={() => setLocale(isZh ? "en-US" : "zh-CN")}
                aria-label={isZh ? "切换到 English" : "Switch to Chinese"}
                title={isZh ? "切换到 English" : "Switch to Chinese"}
              >
                {isZh ? "中" : "EN"}
              </button>
            </div>
          </div>
        </aside>

        <main className={styles.main}>
          <div className={styles.mobileTopbar}>
            <Dialog.Root open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
              <Dialog.Trigger asChild><button type="button" className={styles.mobileMenuButton} aria-label={isZh ? "打开导航" : "Open navigation"}><MenuIcon /></button></Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className={styles.dialogOverlay} />
                <Dialog.Content className={cx(styles.dialogContent, styles.navDialogContent)}>
                  <div className={styles.dialogHeader}>
                    <Dialog.Title className={styles.dialogTitle}>{messages.common.appName}</Dialog.Title>
                    <Dialog.Close asChild><button type="button" className={styles.iconButton} aria-label={isZh ? "关闭导航" : "Close navigation"}>×</button></Dialog.Close>
                  </div>
                  {navContent(true)}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <Link href="/" className={styles.mobileBrand}>{messages.common.appName}</Link>
            <div className={cx(styles.localeSwitch, styles.mobileLocaleSwitch)} aria-label={messages.common.language}>
              <button type="button" className={cx(styles.localeButton, locale === "zh-CN" && styles.localeButtonActive)} onClick={() => setLocale("zh-CN")}>中</button>
              <button type="button" className={cx(styles.localeButton, locale === "en-US" && styles.localeButtonActive)} onClick={() => setLocale("en-US")}>EN</button>
            </div>
          </div>

          <CompactPageHeader
            title={title}
            subtitle={subtitle}
            actions={actions}
          />

          <div className={styles.contentGrid}>
            <div className={cx(styles.content, contentMode === "reading" && styles.contentReading)}>{children}</div>
          </div>
        </main>
      </div>
    </Tooltip.Provider>
  );
}

function NavIcon({ navKey, className }: { navKey: WorkspaceNavKey; className?: string }) {
  const common = { className, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (navKey === "dashboard") return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>;
  if (navKey === "strategies") return <svg {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 16 9 5 9-5" /></svg>;
  if (navKey === "stockBaskets") return <svg {...common}><path d="M4 8h16l-1.5 12h-13L4 8Z" /><path d="m8 8 4-5 4 5" /><path d="M9 12v4M15 12v4" /></svg>;
  if (navKey === "backtests") return <svg {...common}><path d="M4 19V5" /><path d="M4 19h16" /><path d="m7 15 4-4 3 2 5-7" /></svg>;
  if (navKey === "backtestTasks") return <svg {...common}><path d="M5 5h14v14H5z" /><path d="M8 9h8M8 13h5M8 17h7" /></svg>;
  if (navKey === "research") return <svg {...common}><path d="M9 3h6" /><path d="M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3" /><path d="M8 15h8" /></svg>;
  return <svg {...common}><path d="M3 7h18v12H3z" /><path d="M16 11h5v4h-5a2 2 0 0 1 0-4Z" /><path d="M5 7V5h13v2" /></svg>;
}

function ChevronIcon({ direction }: { direction: "left" | "right" }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={direction === "left" ? "m15 18-6-6 6-6" : "m9 18 6-6-6-6"} /></svg>;
}

function MenuIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
}
