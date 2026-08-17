import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { executionMode } from "../../../../../lib/supervisor-experience";

export default function NewEvaluationLayout({ children }: { children: ReactNode }) {
  if (executionMode() === "RECORDED") redirect("/recorded-snapshot");
  return children;
}
