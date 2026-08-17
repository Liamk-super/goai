import type { Metadata } from "next";
import { HitPredictorDemoReport } from "../../../../components/reports/demo/HitPredictorDemoReport";
import { demoCopy } from "../../../../lib/hit-predictor-demo-data";

export const metadata: Metadata = {
  title: demoCopy.mainMetadataTitle,
  description: demoCopy.mainMetadataDescription,
};

export default function HitPredictorDemoPage() {
  return <HitPredictorDemoReport />;
}
