import type { Metadata } from "next";

// The page itself is a client component and cannot name its own title.
export const metadata: Metadata = { title: "History" };

export default function HistoryLayout({ children }: LayoutProps<"/history">) {
  return children;
}
