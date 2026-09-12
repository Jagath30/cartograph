import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cartograph",
  description:
    "Natural-language querying of complex data warehouses, with schema-graph retrieval and explainable join-path selection.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
