import type { Metadata } from "next";

// The page itself is a client component and cannot name its own title.
export const metadata: Metadata = { title: "Ask" };

export default function AskLayout({ children }: LayoutProps<"/ask">) {
  return children;
}
