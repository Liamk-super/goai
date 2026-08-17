import { PublicWheelLanding } from "../../components/landing/PublicWheelLanding";

export default async function LandingPage({
  searchParams,
}: {
  searchParams: Promise<{ start?: string | string[] }>;
}) {
  const { start } = await searchParams;
  return <PublicWheelLanding startOpen={start === "1"} />;
}
