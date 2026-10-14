import Link from "next/link";

import { Glyph } from "./Glyph";

export const REPOSITORY = "https://github.com/Jagath30/cartograph";

/** The width of the page's content, which the header and footer share. */
export const WIDTH = "mx-auto w-full max-w-[1880px] px-5";

const LINK = "rounded-sm underline-offset-4 hover:underline";

/** The frame's top line. Not a heading: each page has its own one h1. */
export function Header() {
  return (
    <header className="border-b border-gray-200">
      <div className={`${WIDTH} flex items-center justify-between py-3`}>
        <Link href="/ask" className={`flex items-center gap-2 text-gray-900 ${LINK}`}>
          <Glyph />
          <span className="text-lg font-semibold tracking-tight">Cartograph</span>
        </Link>
        <a href={REPOSITORY} className={`text-sm text-gray-700 ${LINK}`}>
          Source
        </a>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-12 border-t border-gray-200">
      <p className={`${WIDTH} py-4 text-xs text-gray-600`}>
        Built by Jagath S Manjunath &middot;{" "}
        <a href={REPOSITORY} className={LINK}>
          Source on GitHub
        </a>{" "}
        &middot; Data: a synthetic TPC-DS retail warehouse
      </p>
    </footer>
  );
}

/** First in the page, seen only when focused: straight to the content. */
export function SkipLink() {
  return (
    <a
      href="#main"
      // not-sr-only takes the padding away, so it is given back on focus.
      className="sr-only rounded bg-white text-sm text-gray-900 shadow-md focus:not-sr-only focus:absolute focus:left-3 focus:top-2 focus:z-50 focus:px-4 focus:py-2"
    >
      Skip to main content
    </a>
  );
}
