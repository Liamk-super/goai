import { redirect } from "next/navigation";

export default function OpsHome() {
  redirect("/audit/events");
}
