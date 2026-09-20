---
type: adr
id: FOUNDRY-ADR-0004
title: "Distribution : monorepo claude-plugins (Foundry n'est plus son propre marketplace)"
status: accepted
date: 2026-07-02
supersedes: null
amends: FOUNDRY-ADR-0001
superseded_by: null
deciders: ["pato"]
context_tags: [distribution, marketplace, monorepo, plugin]
---

# FOUNDRY-ADR-0004 — Monorepo `claude-plugins`

> Amende FOUNDRY-ADR-0001, Décision #5 (« Repo = son propre marketplace »). Le rejet de
> `claude-project-template` (Alternatives écartées d'ADR-0001) reste valide et n'est PAS
> touché : un plugin *installé* ≠ un template *copié*.

## Contexte

ADR-0001 a posé « repo = son propre marketplace » : le repo Foundry porte lui-même le
catalogue `patolabs`. Un second plugin arrive — **ship-ios** (la boucle de release iOS) —
et deux frictions apparaissent :

1. **Collision de nom.** Claude Code n'enregistre qu'un seul marketplace par nom ;
   deux repos déclarant `patolabs` se remplacent l'un l'autre à l'`add`.
2. **Contrat partagé, artefacts séparés.** ship-ios consomme le tier query de Foundry
   (`query changelog`). Livrer ce contrat a imposé une danse en **deux temps sur deux
   repos** (Foundry pose le seam v0.5.0, puis ship-ios le consomme) là où un seul commit
   cohérent suffirait s'ils étaient co-localisés. Toute paire de plugins qui se parlent
   subira cette danse.

Besoin cible : distribution unifiée (un seul `marketplace add`) et co-évolution atomique
des plugins liés par un contrat.

## Décision

Un **monorepo `patobiskoto/claude-plugins`** porte le catalogue à la racine et chaque
plugin dans un sous-dossier. C'est le pattern des marketplaces établis (claude-plugins-
official).

```
claude-plugins/
  .claude-plugin/marketplace.json   # catalogue patolabs, metadata.pluginRoot "./plugins"
  plugins/
    foundry/                        # tooling/ skills/ hooks/ agents/ docs/adr/ tests/
    ship-ios/                       # skills/ templates/ scripts/
  .github/workflows/ci.yml          # une CI qui teste chaque plugin
```

- Le **catalogue** liste `foundry` (`source: plugins/foundry`) et `ship-ios`
  (`source: plugins/ship-ios`). Un seul `add` les offre tous les deux.
- Le **versioning reste indépendant** : chaque `plugin.json` garde sa version et son
  `CHANGELOG`. Les « deux cycles de vie » d'ADR-0001 survivent — au niveau des versions
  de plugin, pas des repos.
- **Remplace** la clause ADR-0001 #5 « repo = son propre marketplace » : le marketplace
  vit dans le monorepo, Foundry y est un plugin parmi d'autres.

## Ce qui NE change PAS

- Foundry reste un **plugin installé et mis à jour centralement** — le rejet d'ADR-0001
  de fusionner dans `claude-project-template` (contenu *copié* par projet) tient
  intégralement : un monorepo de plugins *installés* n'est pas un template *copié*.
- Le pipeline, les invariants en code, les tiers query/write, les hooks : inchangés.

## Conséquences

**Devient possible :** un seul `marketplace add` pour toute la famille patolabs ; des
changements cross-plugin atomiques (un contrat partagé se met à jour en un commit) ; une
CI racine unique.

**Dette de migration à solder :** déplacer Foundry dans `plugins/foundry/` casse le
mapping registre repo→projet (le basename passe de `foundry` à `claude-plugins`) et les
chemins CI/`.githooks` — à re-enregistrer et re-câbler une fois. Le repo `foundry`
autonome est archivé (ou redirige) après migration ; ses tags/PR restent son historique.

**Devient plus coûteux :** le repo mêle l'historique et les issues de plusieurs plugins —
acceptable pour une **boîte à outils personnelle** (le profil de patolabs), pas pour un
projet OSS public autonome (ce que Foundry n'est pas).

## Alternatives écartées

- **Rester multi-repo + un mince repo-catalogue `patolabs`** — marche et préserve des
  repos séparés, mais conserve la danse cross-repo pour tout contrat partagé ; le gain
  d'atomicité est perdu.
- **Deux marketplaces à noms distincts** — fragmente la marque patolabs et impose deux
  `add`.
- **Faire de Foundry un OSS public autonome** (donc le garder séparé) — non : Foundry est
  né comme outil perso, dogfoodé sur `mdtoc` et `souffle`, pas comme produit à diffuser.
  Si ce choix changeait un jour, il faudrait un nouvel ADR qui supersède celui-ci.
