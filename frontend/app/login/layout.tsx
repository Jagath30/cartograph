import type { Metadata } from "next";

// The page itself is a client component and cannot name its own title.
export const metadata: Metadata = { title: "Sign in" };

export default function LoginLayout({ children }: LayoutProps<"/login">) {
  return children;
}
