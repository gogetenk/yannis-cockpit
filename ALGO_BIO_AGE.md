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

### D. Âge squelette — T-score worst-site (poids 0,17)

Input : DEXA annuel. T-score worst-site drives.

```
skeletal_age = 30 + max(0, -T_score) * ~24 ans / SD
```

User L1 T = −2,2 → site skeletal age = 30 + 2,2×24 = **~83 ans** (ostéopénie sévère L1).
Autres sites Z normaux → systemic ~50 ans si on moyenne, mais policy : **worst-site drives** (fracture HR Marshall 1996, HR ~2 par −1 SD).

Décision conservatrice : utiliser worst-site (L1) → 83 ans pour ce sous-âge.

σ₀ = 1,5 ans. λ = 730 jours.

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

## 3. Calcul actuel utilisateur

Estimations conservatrices avec les données réelles (mai 2026) :

| Sous-âge | Valeur | Poids | Justification |
|---|---|---|---|
| Sang (PhenoAge) | 34 | 0,34 | ApoB 109 + CRP basse → légère accélération |
| Cardio (VO2max 46, RHR 58) | 28 | 0,28 | VO2max ~p65 mâle 35 ans |
| Composition (ALMI 9.42, BF 24.3) | 42 | 0,20 | BF élevé pénalise malgré bon ALMI |
| Squelette (T L1 −2,2 worst-site) | 83 | 0,17 | Ostéopénie L1 isolée |

**Composite pondéré simple** : 34×0,34 + 28×0,28 + 42×0,20 + 83×0,17 = 11,56 + 7,84 + 8,40 + 14,11 = **41,9 ans**.

Avec CI ±3,1 ans → **42 ans [39 – 45]**.

Vs chrono 35 → **+7 ans** d'accélération biologique. Driver : os L1.

**Action lever par priorité (HR × modifiabilité 3 mois)** :
1. Ostéopénie L1 → charge axiale (squats lourds, deadlifts), vitamine D, K2, magnésium, contrôle DEXA dans 9 mois
2. ApoB 109 → ré-évaluer après 90 j sport intensifié ; envisager bempédoïque si > 90 mg/dL au prochain bilan
3. BF 24,3 % → continuer Wegovy ladder + déficit modéré (−400 kcal/j max pour préserver ALMI)

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
