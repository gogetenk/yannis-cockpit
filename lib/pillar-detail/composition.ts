import type { PillarDetail } from "../types";

export const COMPOSITION_DETAIL: PillarDetail = {
  key: "composition",
  title: "Composition corporelle",
  meta: "Withings · dernière mesure 21 avr 2026",
  hero: {
    figure: "24,3",
    unit: "% MG",
    delta_label: "−4,2 pts vs novembre 2025",
    status_label: "Dérive mineure",
    status_off: true,
  },
  trajectory: {
    x_label: "12 mois",
    y_unit: "% MG",
    y_min: 14,
    y_max: 30,
    points: [
      { date: "JUIN '25", value: 28.5 },
      { date: "JUIL", value: 28.3 },
      { date: "AOÛT", value: 28.0 },
      { date: "SEPT", value: 27.6 },
      { date: "OCT", value: 27.2 },
      { date: "NOV", value: 27.0 },
      { date: "DÉC", value: 26.7 },
      { date: "JANV '26", value: 26.4 },
      { date: "FÉVR", value: 25.8 },
      { date: "MARS", value: 25.1 },
      { date: "AVR", value: 24.3 },
      { date: "MAI '26", value: 24.3 },
    ],
    target: { value: 16, label: "cible 16 %" },
  },
  table: [
    { date: "21 avr 2026", value: "24,3", unit: "% MG", delta: "−0,8" },
    { date: "24 mars 2026", value: "25,1", unit: "% MG", delta: "−0,7" },
    { date: "22 févr 2026", value: "25,8", unit: "% MG", delta: "−0,6" },
    { date: "25 janv 2026", value: "26,4", unit: "% MG", delta: "−0,3" },
    { date: "28 déc 2025", value: "27,0", unit: "% MG", delta: "−0,2" },
    { date: "23 nov 2025", value: "27,6", unit: "% MG", delta: "−0,2" },
  ],
  method: [
    {
      heading: "Source",
      body: "Withings Body Scan, impédancemétrie segmentaire 8 électrodes à 50 kHz. Mesure une fois par semaine au lever, à jeun, vessie vide. Le MG % est calculé par l'équation propriétaire Withings, calibrée sur l'hydratation et la masse non grasse segmentaire (bras, tronc, jambes).",
    },
    {
      heading: "Fenêtre & lissage",
      body: "Affichage = moyenne mobile sur 4 mesures, pour atténuer le bruit BIA jour à jour (typiquement ± 0,8 pt selon l'hydratation et le contenu digestif). La trajectoire couvre les 12 derniers mois afin de lire la tendance, pas le point isolé.",
    },
    {
      heading: "Seuils & cible",
      body: "Cible long terme 16 % MG, catégorie « athletic » ACSM pour la cohorte 30-39 ans. Bande conforme ± 1,5 pt autour de la trajectoire idéale Gompertz, dérivée du modèle poids global (75 kg propre). En parallèle, viser VAT < 100 cm² (seuil Withings de risque cardiométabolique).",
    },
  ],
  cross_link: { label: "Voir signal TDEE apparent", href: "/#tdee" },
};
