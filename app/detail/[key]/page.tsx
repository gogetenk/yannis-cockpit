import { notFound } from "next/navigation";
import { readCockpitSnapshot } from "@/lib/cockpit-data";
import { DetailHeader } from "@/components/cockpit/detail/DetailHeader";
import { DetailHero } from "@/components/cockpit/detail/DetailHero";
import { DetailTrajectory } from "@/components/cockpit/detail/DetailTrajectory";
import { MeasurementsTable } from "@/components/cockpit/detail/MeasurementsTable";
import { MethodCard } from "@/components/cockpit/detail/MethodCard";
import { SubTrajectories } from "@/components/cockpit/detail/SubTrajectories";
import type { DetailKey } from "@/lib/types";

export const dynamic = "force-dynamic";

const VALID_KEYS: DetailKey[] = ["composition", "activity", "cardio", "recovery", "wegovy"];

export default async function DetailPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  if (!VALID_KEYS.includes(key as DetailKey)) notFound();
  const { data } = await readCockpitSnapshot();
  const detail = data.pillar_detail?.[key as DetailKey];
  if (!detail) {
    return (
      <>
        <DetailHeader title={key} meta="Pas de données" />
        <p className="detail-empty">Aucun détail disponible pour ce pilier pour l&apos;instant.</p>
      </>
    );
  }
  return (
    <>
      <DetailHeader title={detail.title} meta={detail.meta} />
      <div className="detail-body">
        <DetailHero hero={detail.hero} />
        <DetailTrajectory trajectory={detail.trajectory} />
        {detail.subs && detail.subs.length > 0 && <SubTrajectories subs={detail.subs} />}
        <MeasurementsTable rows={detail.table} />
        <MethodCard sections={detail.method} cross_link={detail.cross_link} />
      </div>
    </>
  );
}
