import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

import { Footer, Header, SkipLink } from "@/components/frame/Frame";
import { SessionProvider } from "@/components/frame/Session";

// Inter, self-hosted: the variable latin face of @fontsource-variable/inter
// 5.3.0 (OFL-1.1, licence beside it in app/fonts/OFL.txt). Served from our
// own build; nothing is fetched from anyone else.
const inter = localFont({
  src: "./fonts/inter-latin-wght-normal.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: { default: "Cartograph", template: "%s · Cartograph" },
  description: "Plain-English questions over a data warehouse, answered in SQL, with every join explained.",
};

/** The frame on every page: a skip link, the header, the page as the
 *  main landmark, and the footer; the session around the header and the
 *  page. */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="flex min-h-screen flex-col bg-white text-gray-900">
        <SkipLink />
        {/* Who is signed in, for the header and for every page. */}
        <SessionProvider>
          <Header />
          {/* tabIndex -1: the skip link moves focus here, not only the view. */}
          <main id="main" tabIndex={-1} className="flex-1 pb-8">
            {children}
          </main>
        </SessionProvider>
        <Footer />
      </body>
    </html>
  );
}
