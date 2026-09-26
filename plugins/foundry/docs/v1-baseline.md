# Baseline 0.9.0 et reliquat V1

Ce document sépare ce qui est déjà livré de ce qui reste à faire pour V1. Il est
un audit de planification de PAT-52, pas une nouvelle preuve d'acceptation, une
opération de tracker ou une réécriture de receipt.

## Périmètre et méthode

La photographie ci-dessous a été relue le 27 septembre 2026 à partir du provider
Linear par `query profile next-issue` et `query issue PAT-10`, des PR GitHub
publiques, et des artefacts versionnés de ce dépôt. Le statut « Foundry » désigne
la projection normalisée renvoyée par le provider; « natif » est l'état du
workflow exposé par ce même provider. Les deux ne doivent pas être confondus avec
une preuve d'acceptation.

Le provider a répondu une fois `HTTP 503` pendant les lectures individuelles
suivantes. L'audit conserve donc les inconnues plutôt que de les combler. Les
liens GitHub sont les preuves publiques durables; les preuves privées restent
signalées comme telles dans les receipts, jamais recopiées ici.

## Tableau de réconciliation

| Issue | PR et SHA de merge | Preuve d'acceptation ou dérogation | État Foundry / AC | État natif observé | Action pour PAT-8 / release 0.9.0 |
| --- | --- | --- | --- | --- | --- |
| [PAT-12](https://linear.app/patolabs/issue/PAT-12) | [#13](https://github.com/patobiskoto/patolabs-plugins/pull/13) · `993451a` | Readback de protection de `main`, décrit dans `public-repository-security.md`. | `done`, 5/5 | `done` | Acquis; exclure du reliquat. |
| [PAT-21](https://linear.app/patolabs/issue/PAT-21) | [#14](https://github.com/patobiskoto/patolabs-plugins/pull/14) · `f132881` | Contrat documenté et testé de reprise sans élargissement de permissions. | `done`, 6/6 | `done` | Acquis; l'incident qu'il a révélé reste traité par PAT-56. |
| [PAT-24](https://linear.app/patolabs/issue/PAT-24) | [#15](https://github.com/patobiskoto/patolabs-plugins/pull/15) · `bd19192` | Double offline, idempotence et capacité après crash. | `done`, 5/5 | `done` | Acquis; ne pas rejouer le test comme livraison produit. |
| [PAT-26](https://linear.app/patolabs/issue/PAT-26) | [#16](https://github.com/patobiskoto/patolabs-plugins/pull/16) · `9c0596e` | Interlock contre la clôture GitHub prématurée. | `done`, 5/5 | `In Progress` | Acquis; conserver l'écart pour PAT-56 et l'incident PAT-10. |
| [PAT-27](https://linear.app/patolabs/issue/PAT-27) | [#18](https://github.com/patobiskoto/patolabs-plugins/pull/18) · `3e2c66b` | Reprise contrôlée après diagnostics épuisés. | `done`, 5/5 | `Backlog` | Acquis; ne pas rouvrir le ticket Backlog historique. |
| PAT-28 / PAT-29 | [#19](https://github.com/patobiskoto/patolabs-plugins/pull/19) · `772bb0b`; [#20](https://github.com/patobiskoto/patolabs-plugins/pull/20) · `03c3532` | Régression de projection Backlog→In Progress; réarmement borné. | tous deux `done`, 5/5 et 7/7 | `In Progress` / `In Progress` | Acquis; prérequis de stabilité, non reliquat V1. |
| PAT-30 / PAT-31 / PAT-32 | [#21](https://github.com/patobiskoto/patolabs-plugins/pull/21) · `9569a8b`; [#22](https://github.com/patobiskoto/patolabs-plugins/pull/22) · `7cafe2e`; [#23](https://github.com/patobiskoto/patolabs-plugins/pull/23) · `4bb7106` | Remédiation/revue bornées, chacune avec 5/5 ou 6/6 AC. | tous `done` | tous `In Progress` | Acquis; ne pas les estimer à nouveau. |
| PAT-33 à PAT-39 | [#24](https://github.com/patobiskoto/patolabs-plugins/pull/24) · `ebd77b9`; [#25](https://github.com/patobiskoto/patolabs-plugins/pull/25) · `a3f4e7f`; [#26](https://github.com/patobiskoto/patolabs-plugins/pull/26) · `3a96ead`; [#27](https://github.com/patobiskoto/patolabs-plugins/pull/27) · `9fb276b`; [#28](https://github.com/patobiskoto/patolabs-plugins/pull/28) · `7db48eb`; [#29](https://github.com/patobiskoto/patolabs-plugins/pull/29) · `81ad8ae`; [#30](https://github.com/patobiskoto/patolabs-plugins/pull/30) · `8b128c5` | Refus de replays ADR et qualification de sérialisation; chacun 5/5 AC. | tous `done` | tous `In Progress` | Acquis, inclus dans la chaîne de migration ADR. |
| [PAT-22](https://linear.app/patolabs/issue/PAT-22) | [#17](https://github.com/patobiskoto/patolabs-plugins/pull/17) · `edea6e3` | Documents ADR versionnés et liés à des témoins. | `done`, 6/6 | `In Progress` | Acquis; base de l'import, pas une migration à relancer. |
| PAT-40 / [PAT-23](https://linear.app/patolabs/issue/PAT-23) / PAT-41 | [#32](https://github.com/patobiskoto/patolabs-plugins/pull/32) · `5cb1c62`; [#31](https://github.com/patobiskoto/patolabs-plugins/pull/31) · `c663a19`; [#33](https://github.com/patobiskoto/patolabs-plugins/pull/33) · `37606ad` | Qualification du readback, import audité de 27 ADR et signalement explicite des conflits. | tous `done`, respectivement 5/5, 6/6, 5/5 | tous `In Progress` | Acquis; conserver les relations/versions inconnues déclarées, sans les inventer. |
| [PAT-10](https://linear.app/patolabs/issue/PAT-10) | [#12](https://github.com/patobiskoto/patolabs-plugins/pull/12) · `f586791` | Merge humain sous dérogation; les commentaires Linear conservent le readback, l'incident et l'absence de receipt d'AC complet. | `done`, **0/10** | `In Progress` (`49aa24ba-…`) | Exception ouverte: garder la dérogation et les 10 AC visibles. Ne jamais en déduire une réussite totale ou une clôture en masse. |
| [PAT-49](https://linear.app/patolabs/issue/PAT-49) | [#34](https://github.com/patobiskoto/patolabs-plugins/pull/34) · `7023e74` | Receipt typé d'override et reprise idempotente. | `done`, 5/5 | `In Progress` | Acquis; mécanisme qui rend l'exception PAT-10 lisible, non une validation rétroactive. |
| [PAT-50](https://linear.app/patolabs/issue/PAT-50) | [#35](https://github.com/patobiskoto/patolabs-plugins/pull/35) · [`4d9b047`](https://github.com/patobiskoto/patolabs-plugins/commit/4d9b0472b473c65bf22d2e1b08a3508be0209de3) | Deux manifests `0.9.0`, migration dual-host et notes de release. Override `release-post-merge-verification`, receipt `acceptance-override:cc373dc…`; vérification post-merge des installations et de `doctor` consignée sur PAT-50. | `done`, 0/4 | `In Progress` | Baseline publiée sous dérogation; 0/4 interdit de présenter PAT-50 comme preuve complète de ses AC. |
| [PAT-42](https://linear.app/patolabs/issue/PAT-42) | [#36](https://github.com/patobiskoto/patolabs-plugins/pull/36) · `6a2f5ac` | R1 reste fermée sur binding ambigu ou dérivé. | `done`, 3/3 | `In Progress` | Acquis; garde de la baseline. |
| [PAT-53](https://linear.app/patolabs/issue/PAT-53) / [PAT-71](https://linear.app/patolabs/issue/PAT-71) | [#39](https://github.com/patobiskoto/patolabs-plugins/pull/39) · `3a4ea97`; [#40](https://github.com/patobiskoto/patolabs-plugins/pull/40) · `7772fd7` | Contrat portable V1, puis correction du document ADR orphelin. | `done`, 4/4 chacun | `In Progress` / `In Progress` | Acquis; PAT-71 n'est pas du reliquat. |
| [PAT-25](https://linear.app/patolabs/issue/PAT-25) | aucune PR | Incident de clôture prématurée conservé; ticket rendu obsolète. | `dropped`, 0/5 | `Canceled` | Archive: ne pas réactiver son ancienne procédure. Son besoin durable appartient à PAT-56. |
| [PAT-56](https://linear.app/patolabs/issue/PAT-56) | aucune PR | Aucun receipt de livraison. PAT-ADR-0006 est seulement `proposed`. | `backlog`, 0/5 | `backlog` | Reliquat V1: décider puis implémenter une synchronisation compatible CAS/preuves, sans écrire à l'aveugle. |

## Ce que couvre réellement 0.9.0

La baseline est la publication du 26 septembre: adaptateur Linear fail-closed,
Documents ADR versionnés, import audité des 27 ADR, binding du dépôt vers Linear,
receipt d'override, garde contre la clôture prématurée et parcours d'installation
Claude Code/Codex. Les versions de package sont `0.9.0`; les notes de publication
et le [changelog](../CHANGELOG.md) sont la description de référence. La référence
GitHub durable relue est le [commit de merge de la PR #35](https://github.com/patobiskoto/patolabs-plugins/commit/4d9b0472b473c65bf22d2e1b08a3508be0209de3).
L'endpoint GitHub Releases et les tags ne renvoyaient pas de release/tag `0.9.0`
au moment de l'audit; ce document ne fabrique donc pas de lien de release.

Cette baseline ne crée pas une autorité pour modifier l'archive YouTrack. Elle reste
une source de migration/audit en lecture seule. Elle ne permet pas non plus de
déduire des AC à partir de `merged=true`: les deux merges ci-dessus qui affichent
encore 0 AC (PAT-10 et PAT-50 dans cette photographie) sont précisément les cas qui
interdisent cette inférence.

## Reliquat, archives et tests

- **Reliquat V1, socle:** PAT-44 précède PAT-54 (reprise après dérive, puis binding
  et bootstrap par dépôt). PAT-54 précède ensuite le grooming portable PAT-55 et les
  états/preuves PAT-56. Le contrat actuel signale ce dernier parcours comme un gap
  pour Linear; toute écriture durable dépend de la décision explicite encore proposée
  dans PAT-ADR-0006. Cette ADR ne donne aucune autorité d'implémentation.
- **Reliquat V1, GitHub Projects:** PAT-65 qualifie le fournisseur puis débloque
  PAT-57 (lecture), PAT-66 (écritures) et PAT-58 (ADR); PAT-66 et PAT-56 débloquent
  PAT-67, le parcours Foundry complet. Ces travaux sont aussi nécessaires au cutover
  PAT-64. Aucun n'est couvert par la baseline Linear 0.9.0.
- **Reliquat V1, release et recette:** PAT-59 unifie releases/milestones/changelog,
  puis PAT-60 qualifie Ship-iOS. PAT-43, PAT-46, PAT-47 et PAT-70 restent des
  corrections/tests de portage ou de cutover; PAT-68 est la conformité déterministe.
  PAT-69 clôture l'Epic de façon auditable. Ils convergent, avec PAT-55/56/58/60/64/67,
  vers la recette réelle PAT-61, qui précède la publication PAT-62.
- **Archives et ressources de test:** PAT-25 est `dropped` par décision explicite du
  mainteneur le 26 septembre: sa procédure In Review/draft est obsolète depuis le
  merge dérogatoire de PAT-10; ses protections encore utiles vont à PAT-56 et PAT-26.
  Les AC de PAT-25 restent non cochés. PAT-6 et PAT-7 sont des ressources de test MCP
  et ne sont ni du produit ni de l'estimation V1. Les preuves privées de cutover/import
  restent hors dépôt; leur existence déclarée n'est pas une donnée publique à
  reconstruire.
- **Écarts de synchronisation et incident YouTrack:** l'incident GitHub/Linear de
  PAT-10 a montré qu'une automation GitHub pouvait passer un ticket à Done au merge
  d'un prérequis. La règle `merge → Done` a été retirée. Un incident distinct est
  consigné dans [`linear-cutover-operations.json`](linear-cutover-operations.json):
  le 26 septembre, une exécution depuis `main` sans marqueur de dépôt a créé
  FOUNDRY-166 à FOUNDRY-172 dans l'archive YouTrack. Le confinement interdit toute
  nouvelle écriture, laisse la suppression au mainteneur, recrée les suivis dans
  Linear (PAT-42 à PAT-48) et confie le durcissement à PAT-43. Le critère PAT-10
  « YouTrack strictement read-only » reste donc non satisfait et justifie sa
  dérogation, sans transformer l'incident en succès.
- **Tests/CI relus:** seuls les SHA `f586791` (PR #12), `3e2c66b` (PR #18),
  `c663a19` (PR #31) et `4d9b047` (PR #35) ont été relus ici. Chacun exposait trois
  check-runs `success`; l'API legacy rapportait `pending`, 0 statut. C'est une lecture
  ponctuelle de ces quatre SHA, pas une affirmation CI globale ni une nouvelle décision
  de merge. Les tests déterministes cités par les issues restent leurs preuves propres.

## Sources versionnées et limites

- [`release-0.9.0.md`](release-0.9.0.md) et
  [`migration-0.9.0.md`](migration-0.9.0.md) décrivent la publication, la migration
  et l'absence de rollback vers YouTrack après les écritures Linear.
- [`linear-tracker.md`](linear-tracker.md) consigne l'incident PAT-10, son rollback
  borné et l'absence de CAS Linear; [`tracker-contract.md`](tracker-contract.md)
  localise le gap de PAT-56.
- Les états et ratios d'AC proviennent du snapshot Linear normalisé du 27 septembre;
  les PR/SHA proviennent de l'API GitHub des PR. Une lecture individuelle a rencontré
  `HTTP 503`: aucun état absent, receipt privé ou détail natif non relu n'est déduit.

Cette réconciliation ne ferme rien. Toute clôture ou correction ultérieure doit être
autorisée séparément, reposer sur ses propres preuves, et ne doit ni réécrire les
receipts ni créer un lien natif `duplicate` non supporté.
