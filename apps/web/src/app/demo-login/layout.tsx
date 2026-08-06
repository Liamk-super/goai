import type { ReactNode } from "react";

import "../(workspace)/globals.css";

export const metadata = {
  title: "LaunchScope local Demo login",
  description: "Local nickname identity for the LaunchScope competition Demo",
};

export default function DemoLoginLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
