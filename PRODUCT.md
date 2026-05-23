# Product

## Register

product

## Users

Single user, private. Yannis, 35 ans, développeur, vit à Dubaï. Tracking santé long terme (poids, nutrition, activité, sommeil, bilans biologiques, DEXA, traitement Wegovy en cours). Sources de données multiples : Withings (balance segmentation, tensiomètre), Huawei Watch GT2 (steps, HR, sommeil, VO2max, workouts), Yazio (food diary via exporter custom existant), bilans labos trimestriels PDF.

Contexte d'usage dominant : consultation rapide debout / en transport / au lever, sur téléphone. Le user code l'app lui-même, l'héberge en local, y accède en mobile-first PWA via Tailscale ou IP locale. Le chat profond (Claude) vit dans Claude Code en parallèle, pas embed dans la UI.

Connaissances santé du user : élevées. Comprend HOMA-IR, ApoB, T-score DEXA, ALMI. Pas de vulgarisation à faire dans les labels.

## Product Purpose

Cockpit santé personnel qui transforme 3 flux bruts (Withings + Huawei + Yazio + labos) en **trajectoire visuelle vs objectifs chiffrés long terme**. Succès = le user prend ses décisions hebdo/quotidiennes (intake, charge entraînement, ajustement Wegovy, hygiène vie) en regardant un seul écran qui répond à la question : *"suis-je sur la trajectoire que je veux ?"*.

Le cockpit n'est pas un tracker (les apps source font ça). C'est une couche de pilotage qui rend visible la dérive avant qu'elle devienne irréversible, et qui rappelle l'objectif long terme (75 kg propre, sortie d'ostéopénie L1, top 5-10% cardio cohorte 35-40 ans) à chaque consultation.

Le chat IA absorbe toute la profondeur analytique. La UI reste un radar haut niveau.

## Brand Personality

**Calme. Précis. Confiant.**

Voice mood : médecin-pair de confiance qui parle factuellement à un autre expert. Direct, chiffré, jamais condescendant, jamais cheerleader. "Tu es 1.2 kg au-dessus de la trajectoire" plutôt que "Continue tes efforts !". Reconnait les écarts sans dramatiser, expose les progrès sans complaisance.

Mood de référence : Withings (sérénité, scoring lisible, prédictions), Linear (densité maitrisée, typo respirée), Apple Watch app de loin (espace blanc, hiérarchie nette), Bloomberg pour la précision numérique. Pas Strava, pas Whoop, pas MyFitnessPal.

## Anti-references

Ce que le cockpit ne doit jamais évoquer :

- **Gamification (Strava, Duolingo, Whoop)** : pas de badges, streaks, célébrations, feux d'artifice, fanfares. Le user a 5 ans d'historique de cycles binge-restriction ; tout renforcement émotionnel positif/négatif sur une métrique journalière est nuisible.
- **SaaS-cream générique** : pas de gradient bleu-violet, pas d'Inter par défaut, pas de tile-icon-heading-text répété, pas de carte arrondie 12px avec ombre douce.
- **MyFitnessPal / Lose-It** : pas de chiffres rouges agressifs sur dépassement, pas de "tu as dépassé ton quota de 47 kcal", pas de feux tricolores anxiogènes.
- **Apple Health cliché** : pas de tile parfaitement carré arrondi centré avec gros chiffre SF-Pro. La fausse élégance Apple est devenue son propre cliché.
- **AI Slop** : pas de hero-metric template (gros chiffre + label + supporting stats + gradient accent), pas de gradient-text, pas de glassmorphism décoratif, pas d'icône SVG générique au-dessus de chaque titre.
- **Dashboard SRE 2 a.m.** : pas de fond noir avec accents néon. Le user ne fait pas du monitoring d'incident.

## Design Principles

1. **Trajectory beats snapshot.** Toute métrique mesurable contre un objectif chiffré s'affiche avec sa courbe réelle, sa courbe idéale, et sa projection. La valeur du jour seule n'est presque jamais l'information utile.

2. **Calme = confiance, agitation = anxiété.** Pas de notification push, pas d'alerte rouge, pas de pulsation animée. Si quelque chose dérive, l'écran le montre par déformation de la trajectoire, pas par signal d'alarme. Le user a besoin de pouvoir consulter sans appréhension.

3. **L'IA pour la profondeur, l'UI pour le scan.** Le dashboard ne tente jamais d'expliquer, juste de montrer. Les questions "pourquoi" et "comment ajuster" vivent dans le chat Claude Code séparé. Cela autorise l'UI à rester radicalement épurée.

4. **Honnêteté chiffrée, pas motivation.** Afficher les déviations factuellement, sans euphémiser ni dramatiser. Pas de "vous progressez bien !" si la régression linéaire montre l'inverse. Pas de "attention dérive critique" si l'écart est dans la bande de bruit physiologique.

5. **Mobile pouce-friendly, densité maitrisée.** Cible primaire : tel tenu d'une main, lu en 5 secondes au réveil. Hiérarchie absolue : trajectoire poids vs objectif → 4-5 piliers couleur-codés en sous-niveau → drill-down par tap. Pas de scroll infini, pas d'onglet caché.

## Accessibility & Inclusion

Standard WCAG AA en garde-fou (contraste 4.5:1 minimum sur texte courant, focus ring visible, navigation clavier fonctionnelle). Pas de besoin spécifique exprimé par l'utilisateur (pas de daltonisme, pas de basse vision déclarée).

Reduced-motion respecté par défaut côté CSS (les courbes apparaissent sans animation si l'OS le demande). Mobile-first signifie aussi : zones tactiles ≥ 44px, pas de hover-only.
