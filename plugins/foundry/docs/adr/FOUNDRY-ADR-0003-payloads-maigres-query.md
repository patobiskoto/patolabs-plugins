---
type: adr
id: FOUNDRY-ADR-0003
title: "Tier query : payloads maigres — résumé d'abord, détail à la demande"
status: accepted
date: 2026-07-02
supersedes: null
amends: FOUNDRY-ADR-0001
superseded_by: null
deciders: ["pato"]
context_tags: [query-tier, tokens, retrieval]
---

# FOUNDRY-ADR-0003 — Payloads maigres du tier query

> Amende FOUNDRY-ADR-0001 sur deux points : la Décision #3 (« JSON riche ») et
> l'alternative écartée « on charge tout et le modèle filtre ».

## Contexte

Le tier query renvoyait le corps complet de chaque issue dans les listes, et le
corps complet de chaque ADR à chaque `query adrs`. Or le retrieval-avant-
raisonnement fait recharger ces payloads par presque tous les skills (frame,
intake, blockers, roadmap, next-issue, groom, merge-pr), à chaque invocation.
À l'échelle d'un backlog réel, c'était le premier poste de consommation de
tokens du plugin — pour des textes dont l'essentiel ne concerne pas le sujet en
cours. L'écosystème a convergé sur le même remède (context-mode : sandbox des
sorties d'outils, résumé dans le contexte, détail interrogeable).

## Décision

**Résumé d'abord, détail à la demande.**

- Les **listes** (`backlog`, `candidates`, `related` de `query issue`) portent le
  signal de raisonnement — champs, liens, `ac_done/ac_total`, `blocked_by`,
  rangs, statuts — et **aucun corps de texte**.
- Le **détail** se charge explicitement : `query issue <ID>` (corps + notes de
  l'issue demandée), `query adr <ADR-ID>` (texte complet d'un ADR).
- `query adrs` devient un **index** (id, titre, statut, ref). La doctrine
  retrieval-avant-raisonnement est intacte et devient à **coût constant** : on
  scanne toujours la liste des décisions ; on ne charge le texte que de celles
  qui contraignent le sujet.
- Corollaire pour les skills : **jamais de verdict sur du texte non chargé** —
  juger la qualité d'AC, un chevauchement ou un périmètre impose le drill-down
  (`groom`, `intake` l'explicitent).

## Conséquences

- Coût en tokens indépendant de la taille du backlog et du nombre d'ADR.
- Un skill qui raisonne sur le texte doit le demander — un aller-retour de plus,
  contre des dizaines de corps inutiles en moins à chaque invocation.
- ADR-0001 est amendé (notes inline aux deux clauses concernées) ; sa promesse
  « zéro décision, zéro prose » du tier query est inchangée.

## Alternatives écartées

- **Charge-tout d'origine (« le modèle filtre »)** — tenable à 15-30 ADR courts,
  faux en pratique dès que les corps s'allongent : le coût est payé à chaque
  invocation de chaque skill, pour un bénéfice marginal nul.
- **Embeddings / index sémantique** — toujours non (même conclusion
  qu'ADR-0001) : l'index titre+statut suffit au tri, le modèle choisit quoi
  charger. Revisiter si les titres cessent de porter le sens.
- **Un flag `--full` sur les listes** — conserve le chemin coûteux et invite à
  s'en servir ; le drill-down par id est plus simple et plus honnête.
