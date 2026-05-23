# Signaux croisés — spec algorithmique

Section "Signaux" du cockpit. 5 indicateurs cross-source basés sur Yazio + Withings + Huawei avec fenêtres glissantes. Chaque signal a sa méthode de calcul, ses seuils peer-reviewed, et ses limites connues. Audit méthodologique : voir conversation Claude 2026-05-23.

## 1. TDEE apparent (Yazio × Withings)

### Formule

```
TDEE_apparent = mean(intake_28j) + (Δweight_EMA7 × 6500) / 28
```

- **Fenêtre** : 28 jours (minimum 21 ; 14j carry ~30-40% relative error sur le déficit).
- **Lissage poids** : EMA 7j sur Withings (α≈0,25). Utiliser endpoints à J0 et J28.
- **Coefficient kcal/kg** : **6 500** (perte mixte sous semaglutide ; ~25-40% lean mass + ~60-75% fat). Wishnofsky 7700 surestime. Hall 2008 dynamic model donne 6750-7200 chez obèses hypocaloriques ; Heymsfield 2024 meta GLP-1 ajuste à 6300-6800.
- **Exclure les 14 premiers jours** après tout changement de dose (water/glycogen flush domine).

### Affichage

`TDEE apparent : 2 270 ± 180 kcal/j (IC 68%)`

Sub-line : "28 j · coeff 6 500 (Wegovy) · biais log ~20 %"

### Biais self-report

**Ne pas corriger automatiquement.** Yazio (et tout food log) sous-déclare ~15-30% en utilisateurs motivés (Schoeller 1995, Teixeira 2018), jusqu'à 47% chez "diet-resistant" (Lichtman NEJM 1992). Étiqueter "**TDEE apparent**" et indiquer le biais en sub-line.

### Sources
- Wishnofsky M. *Am J Clin Nutr* 1958
- Hall KD. *Int J Obes* 2008 ; *Lancet* 2011
- Hall & Chow. *AJCN* 2013
- Schoeller DA. *Metabolism* 1995
- Heymsfield SB et al. *Diabetes Obes Metab* 2024 (meta GLP-1 body comp)
- Wilding JPH (STEP-1). *NEJM* 2021

---

## 2. Protéines / LBM (Yazio × DEXA)

### Formule

```
Primary   : mean(protein_28j, g/kg_LBM/j)
Zones     : <2,0 = insuffisant | 2,0–2,4 = adéquat | ≥2,4 = optimal
Secondary : count(deficit_days < 1,8 g/kg LBM, sur 28j) ; flag si >6/28
Tertiary  : Δ(jours_train − jours_repos), g/kg LBM ; flag si ≤ 0
```

LBM = lean body mass DEXA (refresh trimestriel). Si BIA Withings, appliquer offset calibration DEXA.

### Pourquoi g/kg LBM et pas g/kg BW

Au BF 24,3 %, le BW-scaled sous-prescrit légèrement. Helms 2014, Aragon/Schoenfeld 2017 : g/kg FFM est le standard pour objectifs lean-focused. Pour ce user : ALMI 9,43 × hauteur² → LBM ~58-62 kg → cible 1,7-2,0 g/kg BW équivalent.

### Pourquoi pas "% jours hit cible"

**Arbitraire** : zéro RCT n'ancre 80% (22/28). La MPS intègre sur 24-48h (Areta 2013, Macnaughton 2016) ; un jour bas n'est pas catastrophique, c'est le **mean chronique** qui drive l'anabolisme. La variabilité haute (CV >25%) émousse l'intégration MPS, d'où le sous-métrique "deficit days".

### Pourquoi le delta jour-train inversé est grave

Sur jours d'entraînement, on devrait avoir **+0,2 à +0,4 g/kg** (Kerksick 2018, Schoenfeld 2018 per-meal 0,4 g/kg × 4). Un −0,3 g/kg le jour exact où la MPS demande un boost = vecteur sarcopénie mesurable sous appétit-coupé GLP-1 (Christensen 2022, Prado 2024).

### Affichage

`Protéines / LBM : 1,9 g/kg LBM · zone adéquate`

Sub-line : "zone adéquate 2,0–2,4 · jours train −0,3 = risque sarcopénie"
Status : `à surveiller` (le delta jour-train est le risque, pas le mean).

### Sources
- Jäger R et al. ISSN position. *JISSN* 2017
- Helms ER, Aragon AA, Fitschen PJ. *JISSN* 2014
- Phillips SM, Van Loon LJ. *J Sports Sci* 2011
- Longland TM et al. *AJCN* 2016
- Pasiakos SM et al. *FASEB J* 2013
- Christensen P et al. *Obes Rev* 2022
- Prado CM et al. *Lancet Diab Endocrinol* 2024
- Almandoz JP et al. *Obesity Pillars* 2024 (GLP-1 + protéines clinical guidance)

---

## 3. Alcool (Yazio)

### Formule

```
unit         : 10 g éthanol (FR/UK SPF)
grams/boisson : volume_ml × ABV × 0,789 (densité éthanol)
current      : sum(grams, 28j) / 4 = u/sem
baseline     : mean(grams, 84j hors 28j courants) / 4 ; SD calculée
drift flag   : current > baseline_mean + 1,5 × SD
```

### Bug à corriger côté pipeline

**Critique** : si Yazio compte "drinks" sans conversion volume × ABV, l'estimation grams est off **30-100%**. Vérifier que l'export Yazio expose le volume + ABV par boisson, sinon implémenter table de conversion par catégorie (bière 5%, vin 12%, spiritueux 40%).

### Seuils (CCSA 2023, le plus solide en 2024)

| Zone | u/sem | Interprétation |
|---|---|---|
| Vert | ≤ 2 | Risque faible |
| Jaune | 3–6 | Risque modéré |
| Ambre | 7–14 | Risque élevé |
| Rouge | > 14 | Risque très élevé |

NHS 14u = ceiling pas cible. WHO 2023 dit "no safe level" mais cliniquement la tier CCSA est la plus actionable.

### Drift detection

Fenêtre 28v28 trop bruité (CV intra 25-40% en social drinker, Kuntsche & Labhart 2013). Utiliser **84j baseline + 28j courant + z-score** (SPC standard, Tennant BMJ 2007). Drift flag à |z| > 1,5.

### Ajustement Wegovy

GLP-1 réduit cravings et intake alcohol de ~30% (Klausen 2022, Hendershot JAMA Psy 2025, Engel 2023). Si l'intake **stable ou rising** sous semaglutide = flag amber (non-response comportemental ou compensation). Bonus flag : binge day si ≥5 u en une journée (NIAAA def).

### Affichage

`Alcool : 9 u/sem · zone ambre`

Sub-line : "CCSA 2023 · zone ambre 7–14 u/sem · stable vs baseline 84 j"

### Sources
- Paradis C et al. CCSA 2023 (Canada Guidance on Alcohol)
- Anderson BO et al. *Lancet Public Health* 2023 (WHO no-safe-level)
- Santé Publique France 2017 (repères français)
- Hendershot CS et al. *JAMA Psychiatry* 2025 (semaglutide × alcool RCT)
- Klausen MK et al. *JCI Insight* 2022
- Kuntsche E, Labhart F. *Addiction* 2013

---

## 4. Dette sommeil × HRV (Huawei × Huawei)

### Formule

```
T_personal    : mean(TST_weekend_unconstrained, 90j)  // fallback 7,5h
SleepLoad7    : Σ (TST_i − T_personal) sur 7j
HRV_smooth    : mean(RMSSD_nocturnal, 7j)
HRV_ref       : mean(RMSSD_nocturnal, 28j)
HRV_sd        : SD(RMSSD_nocturnal, 28j)
HRV_z         : (HRV_smooth − HRV_ref) / HRV_sd

Flag "à surveiller" si :
  SleepLoad7 < −max(2,5 h, 1×SD_perso TST hebdo)
  AND HRV_z < −1
  AND persiste ≥3 jours consécutifs   // débounce bruit PPG
```

### Cible sommeil personnalisée

7,5h générique → personnaliser via free-sleep weekend mean (Klerman & Dijk 2005). Variabilité inter-sujet ~1h SD ; le user peut avoir un besoin physiologique 7,2 ou 8,4h. Le 7,5h est un fallback acceptable mais inférieur.

### Pourquoi pas "−10% baseline" sur HRV

Arbitraire. CV nuit-à-nuit RMSSD est ~10-15% même sur ECG chest strap (Al Haddad 2011) ; plus haut sur PPG poignet. Le 10% misfire pour low-variability users (false neg) et high-variability (false pos). Standard Plews & Laursen 2017 = **z-score |z| > 1 vs rolling SD personnel**, ou SWC = 0,5 × between-day SD.

### Validité PPG Huawei GT2

**Pas validé en peer-review.** Bourdillon 2022, Stone 2021 sur Garmin/Apple/Empatica : LoA ±15-30 ms vs ECG, ICC 0,6-0,85 supine/rest, dégrade vite avec mouvement. Huawei GT2 = même classe consumer. **Reporter trend only, jamais absolu ms vs norms population.**

### Affichage

`Dette sommeil × HRV : −3 h 20 / 7 j`

Sub-line : "z = −1,2 vs perso 28 j · HRV PPG trend only"
Status : `à surveiller`

### Sources
- Van Dongen HP et al. *Sleep* 2003 (cumulative sleep restriction)
- Klerman EB, Dijk DJ. *Curr Biol* 2005 (sleep need personalization)
- Hirshkowitz M et al. NSF. *Sleep Health* 2015
- Watson NF et al. AASM. *Sleep* 2015
- Plews DJ et al. *Sports Med* 2013
- Plews DJ, Laursen PB. *IJSPP* 2017
- Buchheit M. *Front Physiol* 2014
- Bellenger CR et al. *Sports Med* 2016 (HRV + overreaching meta)
- Bourdillon N et al. *Sensors* 2022 (PPG validation)
- Stone JD et al. *J Sports Sci* 2021 (wrist HRV validation)

---

## 5. Réponse Wegovy (Withings × STEP trials)

### Formule corrigée

Référence par **semaine de titration**, pas semaine absolue (semaglutide PK steady state ~4-5 sem par dose) :

| Titration week | Dose | Expected %loss (mean) | SD |
|---|---|---|---|
| W2 | 0,25 mg | −1,0 % | 1,5 |
| W4 | 0,25 → 0,5 | −1,8 % | 1,8 |
| W6 | 0,5 mg | −2,6 % | 2,0 |
| W8 | 0,5 → 1,0 | −3,7 % | 2,2 |
| W12 | 1,0 mg | −5,7 % | 2,6 |
| W20 | 1,7 → 2,4 | −10,6 % | 3,5 |

Données extraites de STEP-1 Fig. 2A (Wilding NEJM 2021) + STEP-4 run-in (Rubino JAMA 2022).

```
expected_pct = lookup_table(titration_week)
actual_pct   = (weight_today − weight_W0) / weight_W0 × 100
z = (actual_pct − expected_pct) / SD(titration_week)
```

### Labels honnêtes

| z | Label |
|---|---|
| \|z\| < 0,5 | "Sur trajectoire attendue" |
| 0,5 ≤ z < 1,0 | "Au-dessus moyenne · valeur prédictive limitée à ce stade" |
| z ≥ 1,0 | "Au-dessus moyenne (top ~16%) · corrélation W68 faible (r≈0,3)" |
| z ≤ −1,0 | "Sous moyenne · revoir adhérence / nausée / intake" |

### Ce qui était faux dans la v1

1. **Courbe Gompertz** `11.3 × exp(-(t/22)^1.4)` était une approximation maison, pas un fit STEP-1. Asymptote 11,3 kg ≈ 12,8% sous-estime le 14,9% observé à W68.
2. **Extrapolation 2,4 mg → 0,5 mg invalide** : STEP-1 publie le trajectoire titration-composite, pas dose-fixe.
3. **"Top quartile" à 1,20× était faux** : à W5,4 sur 0,5 mg, SD inter-sujet est ~2 pp ; top quartile demanderait 1,35-1,50× le mean, pas 1,20×.
4. **Early-phase confound** : weeks 1-4 dominés par water/glycogen, surtout à dose 0,25-0,5 mg. Comparer à toute courbe steady-state surestime "responder" status.
5. **Predictive value early → late** : r ≈ 0,3-0,4 entre W4 et W68 (Wilding 2021 sub-analysis). Early response = signal faible, pas prédiction.

### Calcul pour ce user

- Baseline poids : 86,3 kg (14 avril 2026)
- Aujourd'hui (22 mai 2026) : 83,9 kg
- Perte : −2,4 kg = **−2,78 %**
- Titration week : 6 (fin de 0,5 mg, 2e injection)
- Expected (table) : −2,6 % ± 2,0
- z = (−2,78 − (−2,6)) / 2,0 = **−0,09**
- Label : **"Sur trajectoire attendue"** (pas top quartile)

### Affichage

`Réponse Wegovy : +0,1 z STEP-1`

Sub-line : "−2,4 kg réel vs −2,2 prédit STEP-1 W5,4 · z = +0,1"
Status : `sur trajectoire`

### Sources
- Wilding JPH et al. (STEP-1). *NEJM* 2021;384:989
- Davies M et al. (STEP-2). *Lancet* 2021;397:971
- Rubino D et al. (STEP-4). *JAMA* 2022;327:138
- Garvey WT et al. (STEP-5). *Nat Med* 2022;28:2083
- Friedrichsen M et al. (PK/PD). *Diabetes Obes Metab* 2021;23:754

---

## Audit méthodologique — résumé

| Signal | v1 (avant audit) | v2 (corrigée) | Bug critique |
|---|---|---|---|
| TDEE | 2 350 kcal, coeff 7700, 14j | 2 270 ± 180, coeff 6500, 28j | Coeff Wishnofsky obsolète + fenêtre trop courte |
| Protéines | 22/28 j ≥ 1,4 g/kg BW | 1,9 g/kg LBM, zone adéquate | Cible trop basse + framing % jours arbitraire |
| Alcool | 9 u/sem, limite 14, 28v28 | 9 u/sem, zone ambre CCSA, 84j baseline | Seuils dépassés + fenêtre bruité + bug grammes potentiel |
| Sommeil × HRV | −3h20, HRV 48→42, 10% baseline | −3h20, z = −1,2, PPG trend only | Cible 7,5h générique + seuil HRV arbitraire + PPG non validé |
| Wegovy | 1,20× → top quartile | z = +0,1 → sur trajectoire | Mensonge marketing : user est sur la moyenne |
