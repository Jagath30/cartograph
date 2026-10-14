import Link from "next/link";

import { WIDTH } from "@/components/frame/Frame";

export default function NotFound() {
  return (
    <div className={`${WIDTH} py-10`}>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-3 text-[15px]">
        There is no page at this address.{" "}
        <Link href="/ask" className="rounded-sm underline underline-offset-4">
          Ask a question instead
        </Link>
        .
      </p>
    </div>
  );
}
