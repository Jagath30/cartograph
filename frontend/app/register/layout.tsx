import type { Metadata } from "next";

// The page itself is a client component and cannot name its own title.
export const metadata: Metadata = { title: "Create an account" };

export default function RegisterLayout({ children }: LayoutProps<"/register">) {
  return children;
}
