import type { AppProps } from "next/app";

import BackToTopButton from "@/components/BackToTopButton";
import StockCandleWidget from "@/components/StockCandleWidget";
import { I18nProvider } from "@/i18n/provider";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <I18nProvider>
      <>
        <style jsx global>{`
          html,
          body,
          #__next {
            min-height: 100%;
            width: 100%;
            margin: 0;
            padding: 0;
          }

          html {
            background: #06131a;
          }

          :root {
            --motion-fast: 120ms;
            --motion-surface: 180ms;
            --motion-enter: 240ms;
            --motion-ease: cubic-bezier(.2,.8,.2,1);
            --workspace-gap: 14px;
            --workspace-panel-radius: 15px;
            --workspace-control-height: 38px;
            --workspace-floating-right: max(24px, env(safe-area-inset-right));
            --workspace-floating-bottom: max(24px, env(safe-area-inset-bottom));
            --workspace-market-viewer-bottom: calc(var(--workspace-floating-bottom) + 58px);
          }

          body {
            background: #06131a;
            color: #e2e8f0;
            font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
          }

          a {
            color: inherit;
          }

          input,
          select,
          textarea,
          button {
            font: inherit;
          }

          input::placeholder,
          textarea::placeholder {
            color: rgba(148, 163, 184, 0.9);
          }

          * {
            box-sizing: border-box;
          }

          @media (prefers-reduced-motion: reduce) {
            :root { --motion-fast: 0ms; --motion-surface: 0ms; --motion-enter: 0ms; }
            html { scroll-behavior: auto; }
          }

          .workspace-tooltip {
            z-index: 120;
            max-width: 280px;
            padding: 7px 9px;
            border: 1px solid rgba(71, 85, 105, 0.52);
            border-radius: 9px;
            background: #0f172a;
            color: #e2e8f0;
            box-shadow: 0 10px 30px rgba(2, 6, 23, 0.42);
            font-size: 12px;
            line-height: 1.45;
          }

          @media (min-width: 768px) and (max-width: 1599px) {
            .workspace-market-trigger { width: 44px; height: 44px; padding: 0 !important; justify-content: center; left: 14px !important; }
            .workspace-market-trigger-label { display: none; }
          }
          body:has([data-workspace-sidebar-collapsed="true"]) .workspace-market-trigger { width: 44px; height: 44px; padding: 0 !important; justify-content: center; left: 14px !important; }
          body:has([data-workspace-sidebar-collapsed="true"]) .workspace-market-trigger-label { display: none; }
          @media (max-width: 767px) {
            :root {
              --workspace-control-height: 44px;
              --workspace-floating-right: max(12px, env(safe-area-inset-right));
              --workspace-floating-bottom: max(12px, env(safe-area-inset-bottom));
              --workspace-market-viewer-bottom: calc(var(--workspace-floating-bottom) + 132px);
            }
          }
        `}</style>
        <Component {...pageProps} />
        <StockCandleWidget />
        <BackToTopButton />
      </>
    </I18nProvider>
  );
}
