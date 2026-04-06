import type { AppProps } from "next/app";

import BackToTopButton from "@/components/BackToTopButton";
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
        `}</style>
        <Component {...pageProps} />
        <BackToTopButton />
      </>
    </I18nProvider>
  );
}
