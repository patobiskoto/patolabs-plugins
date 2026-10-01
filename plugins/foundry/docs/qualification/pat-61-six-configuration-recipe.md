# PAT-61 — recette réelle des six configurations V1

Ce document rend PAT-61 exécutable sans présenter une double de transport comme une
preuve fournisseur. La matrice machine est
[`pat-61-six-configuration-v1.json`](pat-61-six-configuration-v1.json). Son état initial
est volontairement `not_run` pour les six cellules : cette livraison ne fabrique aucun
receipt, ne modifie aucun binding et ne conclut pas une qualification réelle.

La recette est une qualification d'intégration, pas un nouveau design d'adaptateur. Les
contraintes existantes de [`tracker-contract.md`](../tracker-contract.md), du contrat de
release [`release-scope.md`](../release-scope.md) et de Ship-iOS restent applicables.
Le gate PAT-68 (`pytest -q -m tracker_conformance tests`) est un préalable de régression
sur ce SHA, mais il n'est jamais une cellule de cette matrice.

## Préconditions et budget

Avant le premier effet, l'opérateur relève le SHA du checkout, les versions affichées
de Foundry et du host, ainsi que le digest des fichiers installés utilisés par le host.
Ils doivent correspondre au SHA qualifié, consigné dans la preuve live hors de ce
modèle versionné ; une distribution installée qui diffère du checkout bloque la cellule.
`adapter_baseline_sha` identifie seulement le merge de PAT-68 à partir duquel cette
recette a été préparée. Cette précondition est importante ici : au préflight du 1er
octobre 2026, Claude Code et Codex affichaient Foundry 0.9.0, mais des fichiers installés
(`foundry_cli.py` et `ghprojects.py`) différaient de `e313f515`; cela ne qualifie aucun
host. Le `pre-push` effectif d'un checkout de sable doit appliquer à la fois le garde
Foundry et le garde privacy local lorsqu'il existe. Le hook privacy de cette machine
chaîne déjà le hook Foundry ; son remplacement supprimerait une protection intentionnelle.

Une cellule reçoit un dépôt, projet tracker, credentials et application iOS de test
explicitement synthétiques. Les deux hôtes peuvent partager un même sandbox fournisseur
s'ils utilisent des issues et branches distinctes et si chaque preuve nomme son hôte.
Chaque cellule comporte un seul chemin nominal et au plus deux reprises
motivées, après relecture qui explique l'effet absent ou ambigu. Une réponse ambiguë non
résolue, une donnée inconnue, un quota non observable ou des credentials absents restent
respectivement `blocked`, `unknown`, `not_observed` ou `blocked`; aucun ne devient
`passed`. Les appels, gestes et quotas ne sont notés que lorsqu'ils sont observables.
La recette n'autorise aucun effet App Store public.

## Matrice de preuve

| Cellule | Ressource à réserver | État de cette livraison | Preuves réutilisables, avec limite |
| --- | --- | --- | --- |
| YouTrack × Claude Code | dépôt et projet YouTrack synthétiques actifs | `not_run` | PAT-59 a lu un périmètre release YouTrack réel ; P64Q est archivé. Ni l'un ni l'autre ne prouve ce parcours ni cet hôte. |
| YouTrack × Codex | même sandbox possible, issues et branches distinctes | `not_run` | mêmes lectures seulement ; aucune preuve Codex live. |
| Linear × Claude Code | nouveau dépôt et projet Linear synthétiques ; ne pas employer le projet PAT actif | `not_run` | le binding PAT/Linear actif est une observation de préflight, pas une recette de sable complète. |
| Linear × Codex | même sandbox possible, issues et branches distinctes | `not_run` | aucune preuve Codex live réutilisable. |
| GitHub Projects × Claude Code | dépôt privé et Project personnel privé synthétiques | `not_run` | GHQUAL-13/PR #14 qualifie le lifecycle common sur Claude ; GHQUAL-15 qualifie la clôture. C'est une base réutilisable, pas une cellule PAT-61 complète. |
| GitHub Projects × Codex | même sandbox possible, issues et branches distinctes | `not_run` | PAT-65 qualifie des primitives API privées/personnelles ; aucune preuve Codex de bout en bout. |

GHQUAL et P64G peuvent être relus comme ressources historiques uniquement. P64Q et
l'ancien binding FOUNDRY sont archivés : ils ne sont pas réactivés et ne constituent pas
un raccourci. Aucun sandbox Linear séparé n'est actuellement enregistré.

## Exécution d'une cellule

1. Créer un checkout synthétique propre, vérifier son identité Git, installer la même
   distribution Foundry que le SHA retenu, vérifier la chaîne de hooks et relire le binding
   exact. Exécuter le gate PAT-68 sur ce SHA avant le chemin live.
2. Sur le tracker de la cellule, faire le chemin nominal complet : binding neuf ; ADR et
   évolution ; Epic, enfants, dépendances et grooming ; start et reprise ; PR, revue,
   CI prouvée sur les deux sources et merge ; clôture d'Epic et projection ;
   `query changelog` puis le bridge Ship-iOS depuis le checkout de l'application de
   test. Conserver les coordonnées native, versions, SHA, identifiants de receipts et
   lecture fraîche, jamais des sorties brutes ou secrets.
3. Exécuter les neuf cas adverses du manifeste. Chacun doit refuser ou isoler l'effet
   hors périmètre : changement de dépôt dans la même session, noms/numéros proches,
   ADR d'un autre produit, configuration globale contradictoire, credentials perdus,
   ADR indisponible, dérive de registre, ancien tracker archivé et rollback de
   configuration. La relecture démontre l'absence de fallback ou d'effet latéral.
4. Pour la frontière Apple, mutualiser seulement le chemin commun : le bridge lit les
   faits tracker de la cellule, l'application de test produit un tag vers Xcode Cloud,
   puis un build TestFlight est lu et validé humainement sur appareil. Les notes,
   screenshots ou soumission non exécutés sont nommés `simulated` ; aucune publication
   publique, soumission App Store ou effet non autorisé n'est effectué. Ship-iOS ne
   détient jamais les credentials tracker.
5. Enregistrer une ligne par étape avec `provider_live`, `simulated`, `blocked` ou
   `unknown`, ses coordonnées, SHA, host/version et limite. La cellule ne devient
   `passed` qu'après toutes les preuves live requises, les tests adverses et la
   validation humaine Apple applicable.

## Bilan de sortie

Le bilan unique est `PASS` seulement si les six cellules ont chacune un receipt live
frais, les étapes exigées du manifeste sont couvertes, le gate PAT-68 est vert au SHA
qualifié et les validations humaines sont consignées. Sinon le bilan est `BLOCKED` avec
la cellule et la preuve absente. Une preuve fournisseur d'un host ne se propage jamais à
l'autre host, et une preuve simulée ne satisfait jamais une étape live.

## Actions live réservées au coordinateur

Le coordinateur doit fournir les ressources synthétiques nécessaires aux six cellules,
rétablir la parité source/distribution et vérifier la chaîne de hooks dans chaque checkout,
exécuter les six chemins et leurs
tests adverses, puis effectuer la validation TestFlight humaine de l'application de
test. Cette branche ne réalise aucune de ces actions.
