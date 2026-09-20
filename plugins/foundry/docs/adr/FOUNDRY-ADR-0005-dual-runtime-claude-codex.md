---
type: adr
id: FOUNDRY-ADR-0005
title: "Distribution dual-runtime : une implémentation, deux packages Claude Code et Codex"
status: accepted
date: 2026-07-21
supersedes: null
amends: FOUNDRY-ADR-0004
superseded_by: null
deciders: ["pato"]
context_tags: [distribution, marketplace, claude-code, codex, portability]
---

# FOUNDRY-ADR-0005 — Une implémentation, deux packages hôtes

> Amende FOUNDRY-ADR-0004 : le monorepo reste le catalogue unique, mais publie désormais
> un catalogue et un manifest natifs pour chacun des deux hôtes.

## Contexte

Le catalogue patolabs a été conçu pour Claude Code. Codex sait désormais installer des
plugins composés de manifests, skills et hooks proches, mais pas identiques : chemins de
catalogue, syntaxe d'invocation, configuration/secrets, approbation des hooks et modèle
d'agent délégué diffèrent. Dupliquer chaque plugin créerait deux implémentations qui
dérivent à chaque correction métier.

## Décision

Le monorepo publie deux façades de distribution sur une seule implémentation :

- `.claude-plugin/marketplace.json` et `.claude-plugin/plugin.json` pour Claude Code ;
- `.agents/plugins/marketplace.json` et `.codex-plugin/plugin.json` pour Codex ;
- `skills/`, `hooks/`, `tooling/`, templates et tests restent partagés ;
- chaque commande de skill utilise `CLAUDE_PLUGIN_ROOT` sous Claude et dérive le chemin
  racine depuis son propre `SKILL.md` sous Codex, où les variables racine ne sont pas
  injectées aux commandes initiées par un skill ;
- la configuration utilise un ordre explicite et portable : environnement, options
  Claude, keychain Foundry, fichier de configuration ;
- les données qui doivent être vues par plusieurs contextes d'exécution — le registre
  repo→projet, lu par les hooks et écrit par les skills — vivent dans un chemin stable
  unique (`FOUNDRY_DATA` explicite, sinon `~/.config/foundry`), jamais dans les dossiers
  privés `PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA` : les hôtes n'injectent ces variables qu'aux
  hooks, pas aux commandes shell des skills, et les honorer scinderait le registre en
  deux vues silencieusement divergentes (amende le foyer `${CLAUDE_PLUGIN_DATA}` de
  FOUNDRY-ADR-0001) ;
- le reviewer Claude reste disponible, mais son contrat est aussi un skill portable que
  Codex peut exécuter dans un sous-agent neuf ;
- les hooks restent communs et fail-open. Leur activation et leur confiance demeurent
  une politique locale de l'hôte ; elles ne sont jamais considérées comme une frontière
  de sécurité.

La syntaxe utilisateur n'est pas masquée : Claude Code invoque `/foundry:<skill>` et
Codex `$foundry:<skill>`. Les noms et le comportement restent identiques.

## Conséquences

**Gains :** une correction métier bénéficie aux deux hôtes, versions et changelogs restent
alignés, et la CI détecte toute divergence de catalogue/manifest. Les worktrees Codex ne
forcent plus un checkout impossible de la branche par défaut.

**Limites assumées :** l'installation doit être testée sur chacun des deux clients ; les
prompts et permissions ne sont pas strictement équivalents ; le keychain automatique est
macOS-only (ailleurs, secret manager ou variable d'environnement) ; un sous-agent Codex
reproduit le contrat du reviewer sans être le même mécanisme qu'un agent Claude ; un hook
non approuvé peut ne pas s'exécuter, donc les invariants critiques restent dans le code.
Codex n'injecte les variables racine qu'aux hooks : la résolution depuis le chemin du
`SKILL.md` reste une étape agentique pour ses commandes, alors que Claude garde son
expansion shell déterministe.

## Alternatives écartées

- **Deux repos ou deux copies de plugin** — isolation maximale, mais dérive immédiate des
  skills, tests et invariants.
- **Une couche de génération de manifests** — prématurée pour deux plugins ; la validation
  de parité est plus simple et garde les fichiers distribués lisibles.
- **Une façade Claude émulée dans Codex** — masquerait les différences de syntaxe et de
  confiance sans les supprimer, et dépendrait de comportements non contractuels.
