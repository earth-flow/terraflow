import type { ReactNode } from "react";

/** Scrollable, centred content area for the non-chat workspace pages. */
export default function PageFrame({ children }: { children: ReactNode }) {
  return (
    <div className="h-full min-w-0 overflow-y-auto px-4 py-6 md:px-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
        {children}
      </div>
    </div>
  );
}
