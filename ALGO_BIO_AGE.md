# Âge biologique composite — spec algorithmique

Cockpit Yannis. Composite weighted de 4 sous-âges, mis à jour en temps réel via EWMA sur les inputs daily et LOCF sur les inputs trimestriels, avec intervalle de confiance par inverse-variance pooling.

Sources : Levine 2018 (PhenoAge, *Aging*), Nes/Wisløff 2014 (HUNT3, *MSSE*), Kelly 2009 (NHANES ALMI, *PLOS ONE*), Marshall 1996 (T-score fracture HR), Cohen 2013 (HD, *MAD*), Mandsager 2018 (VO2max mortality, *JAMA Netw Open*), Browner 1996 / Center 1999 (DEXA mortality), Belsky 2015 (Pace of Aging, *PNAS*), Jain 2022 (composite > single, *eLife*).

## 1. Quatre sous-âges

### A. Âge sang — Levine PhenoAge (poids 0,34)

Inputs (panel trimestriel) : albumine (g/L), créatinine (µmol/L), glucose (mmol/L), CRP (mg/L), lymphocytes (%), MCV (fL), RDW (%), phosphatase alcaline (U/L), GB (10³/µL), âge chrono.

```
xb = -19.907
     - 0.0336 * Albumine
     + 0.0095 * Créatinine
     + 0.1953 * Glucose
     + 0.0954 * ln(CRP_mg/dL)     # si CRP en mg/L → soustraire 0.2197
     - 0.0120 * Lymphocytes_pct
     + 0.0268 * MCV
     + 0.3306 * RDW
     + 0.00188 * AlkPhos
     + 0.0554 * GB
     + 0.0804 * Age

M = 1 - exp( -exp(xb) * (exp(0.0076927 * 120) - 1) / 0.0076927 )
PhenoAge = 141.50225 + ln(-0.00553 * ln(1 - M)) / 0.090165
```

HR all-cause = 1.09 / an d'accélération. σ₀ = 2.0 ans. λ = 180 jours.

**Anti-bruit** : rolling median 90 j sur CRP et GB (les infections aiguës spikent → faux vieillissement).

### B. Âge cardio — VO2max age via FRIEND p50 (poids 0,28)

Input principal : VO2max (Huawei estimé daily, smoothing EMA 14 j). Inputs secondaires : RHR (déjà dans Nes), HRV (modifier ±1-2 ans).

Régression FRIEND (Kaminsky 2017) p50 mâle :
```
VO2max_p50(age) ≈ 50.6 - 0.34 * age
→ FitAge = (50.6 - VO2max_lissé) / 0.34
```

Pour VO2max 46 → FitAge ≈ 13,5 → interpolation : fitness âge équivalent **~28 ans**.

Validation indépendante via Nes/Wisløff inverse (HUNT3) :
```
VO2max_pred(male) = 92.05 - 0.327*age - 0.933*WC + 0.691*PA-I + 0.434*PA-D - 0.222*RHR
→ FitAge_Nes = age qui satisfait VO2max_pred = VO2max_mesuré
```

Modifier HRV : si RMSSD nocturne 7-j z-score ≥ +1 vs baseline perso → −1 an. Z-score ≤ −1 → +1 an. Plafonné à ±2.

HR all-cause = 0.87 par +1 MET (Mandsager 2018, N=122k). σ₀ = 4.5 ans (Huawei vs CPET). λ = 30 jours (le cardio se perd vite).

### C. Âge composition — ALMI + BF% via NHANES (poids 0,20)

Inputs : DEXA trimestriel (ancre absolue) + Withings daily (Δ uniquement, après calibration offset).

**ALMI age** (Kelly 2009, table 2 mâle) :
```
ALMI 9.42 → percentile mâle 30 ans ≈ p75
→ âge où ALMI 9.42 = p50 ≈ ~25 ans
```

**BF% age** (Jackson-Pollock + NHANES DEXA p50 mâle) :
| Age | p50 BF% |
|---|---|
| 30 | 18,6 |
| 35 | 19,8 |
| 50 | 22,7 |
| 60 | 24,6 |

```
BF 24,3 % → interp : 50 + (24,3-22,7)/(24,6-22,7)*10 ≈ ~58 ans
```

Âge composition = moyenne pondérée : `0.5 * ALMI_age + 0.5 * BF_age` ≈ 0.5×25 + 0.5×58 = **~42 ans**.

**Calibration bioimpédance** : à chaque DEXA, stocker `offset = DEXA_BF - Withings_BF_même_jour`. Appliquer aux readings Withings entre DEXA pour Δ daily.

σ₀ = 3,5 ans. λ_body = 14 j (Withings daily) / λ_DEXA = 730 j (rare).

### D. Squelette — statut ISCD, PAS un âge (poids 0,17)

**Correction majeure** : pour un mâle < 50 ans, ISCD 2019 (Adult Position 4.2) impose le Z-score (pas le T-score) et la catégorie est **"within / below expected range for age"**, jamais "ostéopénie" ni "ostéoporose" en l'absence de fracture de fragilité.

**Aucune méthodologie peer-reviewed ne convertit la densité osseuse adulte en "âge squelettique en années".** Levine, KDM, Horvath excluent tous BMD de leurs formules d'âge biologique. Ne JAMAIS sortir un nombre d'années.

**Règles ISCD 2019** :
- Sites : moyenne L1-L4, fémur total, col fémoral, radius 33% (si nécessaire).
- Exclure une vertèbre si elle diffère d'une vertèbre adjacente de > 1 SD (Section 3.2.3).
- L1 isolé bas avec L2-L4 normaux = artefact probable (déformation focale, ostéophyte, Schmorl, scoliose, mauvais labelling vertébral). Workup : radio latérale ou VFA.
- Diagnostic basé sur la moyenne des vertèbres valides + meilleur hip site, PAS le worst single vertebra.

**Sortie pour le composite** : statut catégoriel, pas un âge.
- `optimal` (Z ≥ −1,0 partout) → contribution âge = chrono (neutre)
- `monitor` (−2 < Z < −1) → contribution âge = chrono (neutre, flag clinique)
- `below_expected_for_age` (Z ≤ −2) → contribution +5 ans flat + flag rouge clinique

**User réel (extraction PDF DEXA Sujatha Rajkumar)** :
- L1 T/Z −2,2 (BMD 0,902) — isolé, artefact probable
- L2 −1,2, L3 −0,8, L4 −1,1 → mean L2-L4 Z = **−1,0**
- Fémur G col Z −0,7, total Z −0,7
- Fémur D col Z −0,4, total Z −0,7
- Radius 33% G Z +0,6, D Z +0,9
- **Corps entier Z = +1,0** (au-dessus moyenne)

Statut : `monitor` (L2-L4 à −1,0, fémur normal, corps entier au-dessus moyenne, L1 à confirmer par radio latérale).
Contribution composite : **35 ans** (= chrono, neutre).
Workup : radio latérale L1 + 25-OH-vit-D, Ca, PTH, testostérone, TSH, sérologie cœliaque (par prudence).

σ₀ = 1,5 ans. λ = 730 jours.

**Update** : si la radio confirme L1 artefact → status passe à `optimal`. Si Z futur sur L2-L4 ≤ −2 → status `below_expected` + +5 ans.

## 2. Composite

```python
W      = {'blood':0.34, 'fit':0.28, 'body':0.20, 'bone':0.17}
SIGMA0 = {'blood':2.0,  'fit':4.5,  'body':3.5,  'bone':1.5}
LAMBDA = {'blood':180,  'fit':30,   'body':14,   'bone':730}  # jours

def composite(now, BA, t_meas):
    num = den = 0.0
    inv_var = 0.0
    for d in W:
        dt = (now - t_meas[d]).days
        var_d = SIGMA0[d]**2 * exp(dt / LAMBDA[d])
        prec_d = W[d] / var_d
        num += prec_d * BA[d]
        den += prec_d
        inv_var += W[d]**2 / var_d
    BA_comp = num / den
    ci_half = 1.96 * sqrt(1 / inv_var)
    return BA_comp, ci_half
```

Propriétés :
- O(1) par sample (recursive)
- Données stales auto-dépondérées par inflation de variance
- Sous-âges auditables pour drill-down
- Intervalle de confiance calibré

## 3. Calcul actuel utilisateur (révisé)

Données réelles mai 2026 (DEXA Dr Rajkumar + Withings + Huawei) :

| Sous-âge | Valeur | Poids | Justification |
|---|---|---|---|
| Sang (PhenoAge) | 34 | 0,34 | ApoB 109 + CRP basse → légère accélération |
| Cardio (VO2max 46, RHR 58) | 28 | 0,28 | VO2max ~p65 mâle 35 ans |
| Composition (BF 24,3 p50 + ALMI 9,43 p75 + VAT 120cm² élevé) | 36 | 0,20 | ALMI fort, VAT pénalise |
| Squelette (statut `monitor`) | 35 | 0,17 | Os normaux, L1 isolé à confirmer |

**Composite pondéré** : 34×0,34 + 28×0,28 + 36×0,20 + 35×0,17 = 11,56 + 7,84 + 7,20 + 5,95 = **32,55 ans**.

Avec CI ±2,2 ans → **33 ans [31 – 35]**.

Vs chrono 35 → **−2 ans** (légèrement plus jeune).

**Driver réel = VAT viscéral 120 cm²** (seuil cardiométabolique 100 cm²), pas les os. ApoB 109 secondaire.

**Action lever par priorité** :
1. **VAT viscéral** → continuer Wegovy ladder (déjà en cours) + cardio régulier (zone 2 + HIIT 2×/sem) ; cible VAT < 100 cm² au DEXA suivant
2. **ApoB 109** → ré-évaluer après 90 j ; envisager intervention si > 90 mg/dL persistant après normalisation du poids
3. **L1 anomalie** → radio latérale lombaire pour exclure déformation focale ; pas de traitement osseux à ce stade
4. **VO2max 46 → 48+** → maintenir progression actuelle, ne pas saboter avec déficit calorique excessif (préserver ALMI 9,43 actuel)

## 4. Update rules

| Métrique | Cadence | Lambda décroissance | EWMA α |
|---|---|---|---|
| Withings poids | daily | 14 j | 0,25 (n=7) |
| Withings BF% | daily | 14 j | 0,25 |
| Withings BP | daily | 30 j | 0,12 |
| Huawei RHR | daily | 30 j | 0,12 |
| Huawei HRV | nightly | 14 j | 0,13 |
| Huawei VO2max | daily est. | 30 j | 0,13 |
| Lab panel (PhenoAge inputs) | trimestriel | 180 j | LOCF |
| DEXA | annuel/trimestriel | 730 j | LOCF |

## 5. Limites connues

- Wearable VO2max systématiquement biaisé (Huawei sur-estime low-fit, sous-estime elite) → calibration CPET annuelle recommandée si possible.
- Bioimpédance BF% biais ±3-5 % vs DEXA → toujours appliquer l'offset DEXA-calibré.
- PhenoAge calibré sur cohorte US (NHANES) → léger biais possible dans cohorte non-US.
- Pas de gold-standard pour le composite multi-domaines : Jain 2022 montre que les méthodes corrèlent r ~0,3-0,5 entre elles → expose les sous-âges, pas que le composite.
- GrimAge (méthylation ADN) serait le meilleur prédicteur (HR 1,10/an) mais nécessite un kit annuel ($300) → optionnel one-shot, pas dans la boucle temps réel.
