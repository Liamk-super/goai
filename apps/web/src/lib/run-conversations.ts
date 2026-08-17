import type { ConversationChannel } from "./api-client";

export const RUN_CONVERSATION_CHANNELS: {
  channel: ConversationChannel;
  label: string;
  shortLabel: string;
}[] = [
  { channel: "supervisor", label: "Project lead", shortLabel: "Project lead" },
  { channel: "user-evidence", label: "Target user", shortLabel: "Target user" },
  { channel: "product-engineering", label: "Product manager", shortLabel: "Product manager" },
  { channel: "business-investment", label: "Investor", shortLabel: "Investor" },
];
