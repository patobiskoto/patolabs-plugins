# PAT-62 — rapport final de publication Foundry 1.0.0

Rapport final promis par l'AC6 de PAT-62, rédigé par PAT-93 le 2026-10-04. **PAT-62 a été clos sous un override humain audité, avec 0/8 AC cochés ; ce rapport ne change ni cet état ni sa clôture et ne le rouvre pas.** PAT-51 (Epic) reste ouvert jusqu'au verdict `close-epic` du mainteneur. Ce rapport sépare strictement la preuve avant fusion et la preuve après publication ; il ne contient aucune nouvelle exécution.

## Paquets et versions

| Paquet | Version | Source |
| --- | --- | --- |
| Foundry (Claude Code + Codex) | **1.0.0** | tag `foundry-v1.0.0`, SHA `400fe7450c9a583410973a5f08abf767c9501252` (fusion de la PR #73, PAT-62) |
| Ship-iOS (Claude Code + Codex) | **0.3.0**, inchangé | mêmes manifestes au tag ; aucune API, bridge ou gabarit modifié |

GitHub Release « Foundry 1.0.0 — Ship-iOS 0.3.0 compatible » : publiée le 2026-10-03T17:40:19Z, ni brouillon ni préversion, <https://github.com/patobiskoto/patolabs-plugins/releases/tag/foundry-v1.0.0>. Les catalogues marketplace n'ont pas de clé de version.

## Preuve avant fusion (inchangée)

- **Qualification source PAT-61** : [bilan final](pat-61-final-qualification-report.md), PR #72, 9/9 AC acceptés, 36 étapes réelles. Réutilisée telle quelle, non rejouée ; ses refs producteur restent en source 0.9.0.
- **Installation locale PAT-62** : [observation](pat-62-install-observation.json), marketplace local temporaire, installation propre et mise à niveau 0.9.0 → 1.0.0 (candidat `c9089a13…`, base `16cdaa0a…`), retour arrière exact **vers la base de pré-publication `16cdaa0a…`, pas vers une version 0.9 publique**, Claude Code 2.1.285 et Codex 0.155.1, zéro tour de modèle. `public_git_installation_verified=false` : ce n'est pas une installation publique.

## Couverture des six configurations

Reprise de PAT-61, sans réécriture : YouTrack, Linear et GitHub Projects privé personnel, chacun sur Claude Code et Codex, soit 6 cellules et 36 étapes B/A/G/D/C/R, toutes `passed` dans le parcours composite source borné, avec l'attribution d'hôte d'origine. Cela ne représente ni six nouveaux rejeux autonomes ni six installations de paquet. Détail : [matrice](pat-61-six-configuration-v1.json) et [bilan PAT-61](pat-61-final-qualification-report.md).

## Preuve après publication (PAT-93, 2026-10-04)

Source : [pat-93-post-publication-observation.json](pat-93-post-publication-observation.json) (observation, pas un receipt), zéro tour de modèle. Deux parties : relevé en lecture seule des installations du mainteneur, puis installation propre isolée de la source publique, téléchargée sur autorisation explicite du mainteneur (2026-10-04) dans des répertoires temporaires `CLAUDE_CONFIG_DIR` / `CODEX_HOME`. Les installations réelles du mainteneur n'ont pas été modifiées (relues après coup).

- Manifestes au tag : Foundry 1.0.0 et Ship-iOS 0.3.0 (Claude et Codex).
- Claude Code 2.1.285 : `foundry@patolabs` 1.0.0, `gitCommitSha` `400fe74…`, mis à jour le 2026-10-03T17:41:30Z (71 s après la Release) depuis la marketplace `patolabs` (github `patobiskoto/patolabs-plugins`).
- Codex (codex-cli 0.155.1) : marketplace `patolabs` sur l'URL Git publique, cache `foundry/1.0.0`.
- Intégrité : sha256 de `tooling/foundry_cli.py` `c0aaa7de71daad9e018d385ffa1d3bc82be0bdb532d2dddd0222f6669a2fcee1` au tag et dans les deux caches ; arbres identiques hors `__pycache__` et marqueur `.in_use` (cache Claude seul).
- Depuis chaque copie installée : `registry selection --require-v1` rc 0 (v1, linear, projet PAT) et `doctor` rc 0 ; routes par défaut Claude et Codex résolues.
- **Installation propre de la source publique, deux hôtes, gestionnaires officiels, sans édition de cache** : Claude Code 2.1.285 (`claude plugin marketplace add patobiskoto/patolabs-plugins`, `claude plugin install foundry@patolabs` et `ship-ios@patolabs`) et Codex 0.155.1 (`codex plugin marketplace add https://github.com/patobiskoto/patolabs-plugins.git`, `codex plugin add foundry@patolabs` et `ship-ios@patolabs`). Résultat : **Foundry 1.0.0 et Ship-iOS 0.3.0 installés et activés sur les deux hôtes**, au SHA `400fe7450c9a583410973a5f08abf767c9501252` ; arbres `plugins/foundry` et `plugins/ship-ios` identiques au tag (hors `__pycache__` et `.in_use`) ; `foundry_cli.py` sha256 `c0aaa7de…`; `registry selection --require-v1` et `doctor` rc 0 depuis chaque copie.
- **Paire installée Foundry↔Ship-iOS** : `changelog_bridge.py v1.0.0 --require-v1-binding` depuis chaque paire installée, rc 0, contrat `ship-ios.foundry-changelog-bridge.v1`, mode `foundry-v1`, `foundry_version` 1.0.0, `scope_count=0` et `unavailable=0`. Le périmètre `v1.0.0` de ce dépôt est réellement vide : cela prouve le chemin de capacité de la paire installée, pas un changelog peuplé.

## Limites optionnelles et non observé

1. **Mise à niveau 0.9.0 → 1.0.0 en source publique : non reproductible.** Le dépôt public n'expose que `main` et le tag `foundry-v1.0.0` ; aucun ref 0.9.0, et le gestionnaire Claude refuse un SHA (`Remote branch <sha> not found in upstream origin`) ; le refus côté Codex n'a pas été essayé. Publier un ref pour l'occasion n'a pas été fait. Restent comme preuves : la mise à jour réelle de l'installation Claude du mainteneur le 2026-10-03 (`lastUpdated` 17:41:30Z), dont **la version de départ n'a pas été relevée** (`installedAt` 2026-09-25 précède la publication 0.9.0 du 2026-09-26, donc un départ en 0.9.0 n'est pas établi), et la mise à niveau locale PAT-62 (marketplace local, `public_git_installation_verified=false`).
2. Aucun tour de modèle Claude ou Codex n'a chargé les plugins : toutes les vérifications ont exécuté la CLI ou le pont installés directement (zéro tour sur les deux hôtes).
3. Ship-iOS 0.3.0 a été observé installé dans les installations **isolées** ; il reste absent des installations réelles du mainteneur sur cette machine.
4. Le changelog `v1.0.0` de ce dépôt est vide : la paire installée est prouvée sur son chemin de capacité, pas sur un changelog peuplé.
5. Deux points orange environnementaux, pas des défauts de release : `core.hooksPath` pointe vers un répertoire de hooks global (le pre-push R1 local n'est pas actif sur cette machine) ; les hooks de routage et de garde sont documentés fail-open.
6. Binaire Codex : la vérification a utilisé le binaire du PATH (`codex-cli 0.155.1`), pas l'exécuteur embarqué 0.159.2 relevé par PAT-61 ; la forme courte `codex plugin marketplace add patobiskoto/patolabs-plugins` documentée dans les README n'a pas été exercée (l'URL Git complète l'a été).
7. Le niveau d'effort runtime non exposé par l'hôte reste `unknown`, comme dans PAT-61.

## Résiduel explicitement hors V1

PAT-17 (benchmark économique/modèle étendu), PAT-18 et PAT-19 : V1.1. Ni cockpit, ni nouveau tracker, ni migration générale de données, ni promotion de modèle local, ni soumission App Store. GitHub public, organisations, GitHub Apps et Discussions restent non qualifiés.

## Documents de release figés : corrections par renvoi

`release-1.0.0.md`, `migration-1.0.0.md` et la section `1.0.0` du CHANGELOG sont la preuve publiée dans le tag `foundry-v1.0.0` : l'historique publié est immuable (`tests/test_release_contract.py`, `tests/fixtures/release-history.json`, comme pour 0.9.0). Ils ne sont donc **pas réécrits** et restent identiques au tag. Leur formulation « candidat préparé, publication et lectures d'installation en attente » décrivait l'état avant publication et est **remplacée par le présent rapport et par l'[observation post-publication](pat-93-post-publication-observation.json)**, qui font foi pour l'état publié. Leur empreinte telle que publiée est gelée dans `release-history.json` par PAT-93 ; l'entrée manquait depuis la publication parce que le test qui l'exige est exclu de la CI (`historical_fixture`).

## Statut de documentation (AGENTS.md#R5)

- Mis à jour par PAT-93 : README racine, README Foundry, README et CHANGELOG Ship-iOS (renvoi vers ce rapport) ; entrée `Unreleased` du CHANGELOG Foundry ; gel de `1.0.0` dans `release-history.json`.
- Nouveaux : ce rapport et l'observation post-publication.
- Pas nécessaire, avec raison : `release-1.0.0.md`, `migration-1.0.0.md` et section `1.0.0` du CHANGELOG, qui sont de la preuve publiée figée, corrigée par renvoi ci-dessus et non réécrite ; `pat-61-*` et `pat-62-install-observation.json`, observations historiques ; CLI, constantes, hooks, manifestes et `AGENTS.md`/`CLAUDE.md`, aucune surface publique ni règle modifiée.
- La porte mécanique FOUNDRY-123 n'est pas revendiquée livrée.
