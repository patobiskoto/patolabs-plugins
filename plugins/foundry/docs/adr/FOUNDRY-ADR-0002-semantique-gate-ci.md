---
type: adr
id: FOUNDRY-ADR-0002
title: "Sémantique du gate CI : vert = prouvé vert, sur deux sources"
status: accepted
date: 2026-07-01
supersedes: null
amends: FOUNDRY-ADR-0001
superseded_by: null
deciders: ["pato"]
context_tags: [ci-gate, write-tier, invariant]
---

# FOUNDRY-ADR-0002 — Sémantique du gate CI

> Précise (et amende sur un point) FOUNDRY-ADR-0001, Décision #3, qui énonçait
> « refuse le merge si check-runs ≠ tous `success` ». Tranche l'issue #3.

## Contexte

La formule de l'ADR-0001 est ambiguë face à la réalité de GitHub :

1. Des conclusions `neutral` et `skipped` sont **normales** sur une CI saine
   (jobs conditionnels, workflows filtrés par path). Exiger littéralement « tous
   `success` » bloquerait des merges légitimes.
2. À l'inverse, deux trous ont été démontrés en revue : une liste de check-runs
   **vide** (CI pas encore démarrée, ou absente) et des checks **tous `skipped`**
   (PR docs-only sous workflows path-filtrés) passaient pour verts — un merge non
   prouvé, exactement ce que l'invariant devait empêcher.
3. Le gate ne lisait que l'API **check-runs** ; les CI qui rapportent via l'API
   **commit-status** legacy (Jenkins, CircleCI…) étaient invisibles — un build
   rouge ressemblait à « pas de CI ».

## Décision

**Vert = prouvé vert.** Le verdict `passed` de `write.ci_gate` exige :

- **au moins un check concluant `success`**, toutes sources confondues ;
- **aucun** check `pending` (on attend) ni `failing` (on s'arrête) ;
- `neutral` / `skipped` sont **tolérés à côté** d'un `success` réel, mais ne
  prouvent jamais rien à eux seuls (zéro check ou tous-`skipped` ⇒ refus) ;
- le gate lit **deux sources fusionnées** : l'API check-runs **et** l'API
  commit-status legacy — un statut legacy rouge bloque même sans check-run ;
- l'**unique dérogation** est `--allow-no-ci`, décidée *dans* le gate, valable
  seulement quand **les deux sources sont vides** ET qu'aucun **signe de CI
  active** n'existe pour le commit (`ci_expected` : une check-suite en cours,
  avec des runs, ou en file depuis moins de 15 minutes — la fenêtre post-push
  n'est pas dérogeable ; une suite en file vide et ancienne, laissée par des
  workflows `schedule:`-only, ne compte pas — sinon la dérogation serait
  bloquée à jamais sur ces repos), tracée par `waived=true` dans le verdict.
  Rouge, pending ou tous-`skipped` ne sont jamais dérogeables.

Le merge reste épinglé au sha vérifié par le gate (refus si le head a bougé).

## Limites assumées

- **Un statut legacy `success` posé par un bot non-CI** (CLA-assistant, badge
  codecov reporté…) compte comme preuve : le gate ne peut pas décider ce qui est
  « une vraie CI » côté API. Si ce cas devient réel, la réponse est une liste de
  contextes requis — pas encore justifiée (cf. alternative écartée).
- **Un contexte legacy rouge ou pendu à jamais bloque le merge**, sans dérogation.
  C'est voulu (l'invariant prime) ; l'échappatoire opérationnelle est de re-poster
  un statut `success` sur le contexte depuis l'outil CI, ou de pousser un nouveau
  commit (nouveau sha, nouveaux statuts).
- **Pas de déduplication entre les deux sources** : une CI qui rapporte aux deux
  APIs (état de migration courant) est comptée deux fois dans `total`, et un
  jumeau legacy en retard peut faire attendre un merge dont le check-run est déjà
  vert — un re-essai suffit. Fusionner par nom serait fragile (les noms diffèrent
  souvent entre les deux APIs).
- **La fenêtre post-push des CI legacy-only n'est pas détectable** : Jenkins & co
  ne créent pas de check-suite, donc entre le push et leur premier statut
  `pending`, `ci_expected` ne voit rien — `--allow-no-ci` y reposerait sur le seul
  jugement humain. C'est la limite de l'API ; le skill merge-pr impose d'attendre
  et de demander avant d'utiliser le flag.

## Conséquences

- La clause « tous `success` » d'ADR-0001 est **amendée** en « au moins un
  `success`, rien de rouge ni d'en-cours, le reste toléré » — la doctrine
  rejoint le code et les tests (`tests/test_pure.py`, section ci_gate).
- Un repo sans CI ne merge qu'avec une dérogation explicite et auditée.
- Ajouter un provider code-host impose d'implémenter **les deux** lectures
  (`check_runs` + `commit_statuses`) du port `CodeHost`.

## Alternatives écartées

- **Littéral « tous success »** — casse les CI saines à jobs conditionnels ;
  `skipped` y est un état de fonctionnement normal, pas une anomalie.
- **Liste d'exemptions configurable par check** — de la complexité de
  configuration pour un besoin non démontré ; à revisiter si un cas réel
  l'exige (rester simple tant que l'échelle ne l'impose pas, cf. ADR-0001).
- **Ignorer l'API legacy** — laisse un angle mort entier (Jenkins & co) dans
  l'invariant central du pipeline.
