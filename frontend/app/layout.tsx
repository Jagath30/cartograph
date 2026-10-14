import type { Metadata } from "next";
import "./globals.css";

import { Footer, Header, SkipLink } from "@/components/frame/Frame";

export const metadata: Metadata = {
  title: { default: "Cartograph", template: "%s · Cartograph" },
  description:
    "Ask a data warehouse a question in plain English, and see how the answer was built, and where a choice had no basis.",
};

/** The frame on every page: a skip link, the header, the page as the
 *  main landmark, and the footer. */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col bg-white text-gray-900">
        <SkipLink />
        <Header />
        {/* tabIndex -1: the skip link moves focus here, not only the view. */}
        <main id="main" tabIndex={-1} className="flex-1 pb-10">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
