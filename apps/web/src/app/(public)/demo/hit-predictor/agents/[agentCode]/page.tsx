import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { HitPredictorAgentDemo } from "../../../../../../components/reports/demo/HitPredictorAgentDemo";
import { demoCopy, demoSpecialistByCode, demoSpecialists } from "../../../../../../lib/hit-predictor-demo-data";

export function generateStaticParams() {
  return demoSpecialists.map(agent => ({ agentCode: agent.code }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ agentCode: string }>;
}): Promise<Metadata> {
  const agent = demoSpecialistByCode((await params).agentCode);
  return {
    title: agent ? `${agent.label}${demoCopy.specialistMetadataSuffix}` : demoCopy.specialistMetadataFallback,
    description: agent?.verdict,
  };
}

export default async function HitPredictorAgentDemoPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentCode: string }>;
  searchParams: Promise<{ view?: string | string[] }>;
}) {
  const agent = demoSpecialistByCode((await params).agentCode);
  if (!agent) notFound();
  const requestedView = (await searchParams).view;
  const view = requestedView === "full" ? "full" : "summary";
  return <HitPredictorAgentDemo agent={agent} view={view} />;
}
