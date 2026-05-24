import { readCockpitSnapshot } from "@/lib/cockpit-data";
import { AppHeader } from "@/components/cockpit/AppHeader";
import { Hero } from "@/components/cockpit/Hero";
import { WegovyBanner } from "@/components/cockpit/WegovyBanner";
import { SignalsSection } from "@/components/cockpit/SignalsSection";
import { BiologyCard } from "@/components/cockpit/BiologyCard";
import { BioAgeTile } from "@/components/cockpit/BioAgeTile";
import { PillarTile } from "@/components/cockpit/PillarTile";

export const dynamic = "force-dynamic";

export default async function Page() {
  const { data } = await readCockpitSnapshot();

  return (
    <>
      <AppHeader today={data.today} />
      <Hero hero={data.hero} />
      <SignalsSection signals={data.signals} />
      <WegovyBanner wegovy={data.wegovy} />
      {data.biology && <BiologyCard bio={data.biology} />}
      <main className="grid" aria-label="Piliers santé">
        <BioAgeTile bioAge={data.bio_age} />
        {data.pillars.map(p => <PillarTile key={p.key} pillar={p} />)}
      </main>
    </>
  );
}
