import type { PillarDetail } from "@/lib/types";

interface Props { hero: PillarDetail["hero"] }

export function DetailHero({ hero }: Props) {
  return (
    <section className="detail-hero" aria-label="Valeur actuelle">
      <p className={"detail-hero-status" + (hero.status_off ? " deviation" : "")}>
        {hero.status_label}
      </p>
      {hero.delta_label && <p className="detail-hero-delta">{hero.delta_label}</p>}
      <p className="detail-hero-figure">
        {hero.figure}<span className="unit"> {hero.unit}</span>
      </p>
    </section>
  );
}
