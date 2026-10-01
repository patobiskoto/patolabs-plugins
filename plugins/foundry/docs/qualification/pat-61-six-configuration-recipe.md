# PAT-61 — recette réelle des six configurations V1

Ce document rend PAT-61 exécutable sans présenter une double de transport comme une
preuve fournisseur. La matrice machine est
[`pat-61-six-configuration-v1.json`](pat-61-six-configuration-v1.json). Les cellules
gardent leur état d'exécution observé ; aucune n'est encore `passed` et le manifeste ne
fabrique aucun receipt.

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

Après le merge PAT-76 (`82bf3e5e`), la mise à jour du marketplace Codex a rendu ses
251 fichiers Foundry suivis identiques au snapshot `main`. Le cache installé Claude
Code reste différent sur 76 de ces fichiers bien que `claude plugin update` annonce
0.9.0 à jour. Un préflight Claude Code/Sonnet en lecture seule, avec le répertoire de
plugin du marketplace chargé explicitement, a résolu le dépôt Linear de sable vers
PAT/Linear et exécuté le CLI de ce snapshot sans erreur. Ce préflight ne constitue pas
encore un parcours Claude complet. En outre, depuis ce même dépôt Linear, un appel
Codex avec `FOUNDRY_TRACKER=youtrack` et `PROJECT_REPO` pointant vers le dépôt YouTrack
a quand même relu PAT-74 dans PAT/Linear : ce témoin couvre seulement la contradiction
d'environnement, pas toute la matrice adverse.
Deux autres lectures dans la même session Codex ont résolu PAT-74 dans PAT/Linear et
P61Y-1 dans P61Y/YouTrack depuis leurs checkouts respectifs. La lecture de PAT-74
depuis le checkout YouTrack a échoué `issue unavailable`; depuis le dépôt principal
PAT/Linear, elle a échoué `issue_outside_binding`, car PAT-74 appartient au projet
Linear de sable. Ces lectures étayent l'isolation de binding et de projet, sans prouver
les cas de mutation, de rollback ou de credentials perdus.

Le préflight adverse suivant est attaché au snapshot de plugin `82bf3e5e`. Chaque
résultat est borné à l'opération observée ; les cases non exécutées restent ouvertes.

| Cas | Observation | Limite |
| --- | --- | --- |
| Changement de dépôt, même session Codex puis Claude Code | Codex : PAT-74 et P61Y-1 résolus chacun dans leur projet, PAT-74 refusé depuis P61Y ; Claude Code/Sonnet : dans une seule session, le CLI source a sélectionné `linear/PAT` et relu PAT-74 `done`/accepté, puis sélectionné `youtrack/P61Y` et relu P61Y-2 `review`/acceptation inconnue | Lectures seules avec cwd explicite, aucun write croisé tenté ; coût Claude observé 0,124532 USD au tarif listé |
| Nom/numéro proche | PAT-74 refusé par `issue_outside_binding` depuis le projet PAT principal de même équipe ; GHQUAL-1 (Issue #1 réelle d'un autre dépôt) refusée depuis P61G par `invalid_issue_coordinate` avant lecture native | Deux collisions de coordonnées en lecture seule, aucune mutation croisée |
| ADR d'un autre produit | `query adr PAT-ADR-0006` depuis le sandbox Linear : `ADR introuvable`, index local vide | Pas d'écriture ADR tentée |
| Configuration globale contradictoire | `FOUNDRY_TRACKER=youtrack` et `PROJECT_REPO` YouTrack depuis le checkout Linear résolvent encore PAT-74 dans PAT/Linear ; `FOUNDRY_TRACKER=linear` et `PROJECT_REPO=patolabs-plugins` depuis P61G sélectionnent encore `ghprojects/P61G` | Lectures seules sur deux bindings, aucune écriture |
| Credentials du tracker actif perdus | `FOUNDRY_RUNTIME_CONFIG_ISOLATED=1` sans token en environnement : Linear refuse `LINEAR_API_TOKEN` et YouTrack refuse `YOUTRACK_TOKEN` (URL non secrète conservée) ; GitHub Projects depuis P61G avec `GH_CONFIG_DIR` neuf et sans `GH_TOKEN`/`GITHUB_TOKEN` refuse `project.read: authentication_failed` | Isolation contrôlée des processus, pas révocation des credentials réels ; aucune issue lue |
| ADR déclaré indisponible | Non exécuté | Requiert une fixture ADR déclarée et isolée |
| Dérive du registre/marqueur | Dans une copie Git locale du sandbox Linear, modifier seulement `tracker` invalide le digest du marqueur et refuse `registry selection --require-v1` ; le hook PAT-42 refuse `gh pr create`, `gh pr merge` et `git push origin main`, mais laisse `git status` passer | Commandes dangereuses soumises au hook, jamais exécutées ; aucun provider write |
| Ancien tracker archivé | Dans le checkout migré P64G, la sélection reste `ghprojects/P64G` ; le slot source `youtrack/P64Q` est archivé et `require_writable_project` refuse sa mutation avant appel fournisseur | Préflight local sans tentative d'écriture provider ; aucun binding réactivé |
| Rollback de configuration | Restaurer les octets originaux du marqueur dans cette copie rétablit la sélection PAT/Linear ; relecture byte-identique | Rollback local, pas un cutover fournisseur |

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
| YouTrack × Claude Code | dépôt et projet YouTrack synthétiques actifs | `blocked` | P61Y-3/PR #3 a parcouru reprise, ouverture, review, CI et merge sous Claude en mode source (`1755a3d`) ; le démarrage a été fait par le coordinateur et le cache installé 0.9.0 reste incompatible avec le marqueur v2. Le scope YouTrack classe P61Y-3 `unavailable` ; Apple reste sans preuve. |
| YouTrack × Codex | P61Y et dépôt privé de sable | `in_progress` | P61Y-1/PR #1 et P61Y-2/PR #2 mergées via Foundry avec CI et review ; le scope P61Y-2 passe de `unfinished` à `unavailable`, jamais à `accepted` sans reçu lifecycle exact YouTrack. ADR test créée/éditée ; lien ADR→issue refusé. Epic, adversaires et Apple restent à faire. |
| Linear × Claude Code | projet PAT synthétique et dépôt privé de sable | `in_progress` | Claude Code/Sonnet a démarré PAT-78 et, via les skills Foundry, ouvert la PR #3, obtenu une review indépendante et mergé avec CI verte. Le scope de release lit PAT-78 `accepted`. Le merge a aussi accepté à tort l'ADR synthétique non citée PAT-ADR-0001 ; PAT-79 a corrigé ce défaut sans réécrire l'incident. Le reste du parcours et Apple restent sans preuve. |
| Linear × Codex | projet PAT synthétique et dépôt privé de sable | `in_progress` | PAT-74/PR #1 et PAT-77/PR #2 mergées avec CI et review ; scope release et bridge lisent PAT-77 `accepted` ; ADR test créée/éditée/liée. Supersession, Epic, adversaires restants et Apple restent à faire. |
| GitHub Projects × Claude Code | dépôt privé et Project personnel privé synthétiques | `not_run` | GHQUAL-13/PR #14 qualifie le lifecycle common sur Claude ; GHQUAL-15 qualifie la clôture. C'est une base réutilisable, pas une cellule PAT-61 complète. |
| GitHub Projects × Codex | P61G et dépôt privé de sable | `blocked` | P61G-1 existe, mais le readback `Project.items` reste vide après reprise bornée ; aucune création validée. |

GHQUAL et P64G peuvent être relus comme ressources historiques uniquement. P64Q et
l'ancien binding FOUNDRY sont archivés : ils ne sont pas réactivés et ne constituent pas
un raccourci. Les dépôts privés de sable `foundry-v1-pat61-linear-sandbox`,
`foundry-v1-pat61-youtrack-sandbox` et `foundry-v1-pat61-ghprojects-sandbox` sont
désormais enregistrés avec leurs bindings séparés PAT, P61Y et P61G. Ils ne modifient
pas le binding du dépôt `patolabs-plugins`.

Le préflight live du 1er octobre 2026 a confirmé deux témoins Codex limités :
PAT-74 a été mergé dans le dépôt Linear de sable (PR #1, head `f52efdbc`, merge
`855dd887`, AC 2/2) et P61Y-1 dans le dépôt YouTrack de sable (PR #1, head
`b4724103`, merge `afe441dc`, AC 2/2). Le second a nécessité l'attachement au seul
projet P61Y du prototype YouTrack `GitHub PR` déjà existant. Ces témoins ne couvrent
ni ADR/Epic, ni tous les adversaires, ni le chemin Apple : les cellules restent
`in_progress`. Le premier create P61G-1 a laissé une Issue et un item
consultable depuis l'Issue, mais `Project.items` reste vide ; le rejeu borné a échoué
une seconde fois à `partial_create:item_readback`. Son intention locale est conservée,
sans nouveau retry ni assertion de succès GitHub Projects.

Le second témoin Linear, PAT-77, a d'abord laissé une issue native malgré un retour
`Linear issue divergent after create; no retry` ; le corps relu ne gardait pas le saut
de ligne terminal, sans que la cause exacte du conflit soit attestée. La relecture
Foundry a retrouvé cette seule issue en `ready` ;
aucune création n'a été rejouée. Claude Code a ensuite exécuté son `issue start` sur
le checkout de sable. Son premier `openpr` a été refusé par le sandbox de lecture du
host avant effet ; la tentative corrigée n'a lancé aucun processus Foundry et a été
arrêtée après vérification qu'aucune PR ni transition n'existait. Le coordinateur a
ouvert la PR #2 via Foundry, puis la review indépendante a lié la preuve
`a5bdb9d4` au head `b087948a` et le gate CI a mergé `ecc85f44` (AC 3/3). L'issue
relue est `done`. Avant assignation, le milestone natif `PAT61 sandbox v1`
(`ea006a9b-8b02-47c8-9e96-9af4d73794bf`) donnait un scope explicitement vide
dans `query changelog` et le bridge Ship-iOS. Après assignation de PAT-77 par Foundry,
les deux lectures donnent un seul ticket `accepted`, zéro `unfinished` et zéro
`unavailable`. Ce résultat teste le bridge du dépôt synthétique, pas TestFlight.

Le troisième témoin Linear, PAT-78, a qualifié le host Claude Code/Sonnet sur le
CLI source correspondant à `82bf3e5`. Son unique issue a été relue après le retour
`Linear issue divergent after create; no retry` ; aucune création n'a été rejouée.
Claude a démarré le ticket, puis le témoin README a été commité sur le head
`631bdbd10c21f8a0004b15546af1ed0c22ef41b2`. Le skill `foundry:open-pr` a
ouvert la PR #3. Le check `verify` était vert sur ce head et une review indépendante
a jugé le diff mergeable avant que `foundry:merge-pr` ne merge la PR à
`8772d63f26b58d866643103179a08b65c9d69540`. PAT-78 est `done` (AC 2/2) ; le
scope Linear relu contient PAT-77 et PAT-78 `accepted`, zéro `unfinished` et zéro
`unavailable`.

Le même merge a produit un effet indésirable réel mais limité au sandbox : il a
accepté PAT-ADR-0001, pourtant non citée par PAT-78 et explicitement non
décisionnelle. Le skill a confondu l'index ADR du projet renvoyé par `query issue`
avec les ADR cadrant ce ticket. La relecture du provider confirme le statut
`accepted` et la version `6607e123-b0cd-48c8-b93b-2d6418bdab77` ; aucun rollback
non attesté n'est revendiqué. PAT-79, lié comme dépendance de PAT-61, a depuis été
mergé via Foundry en PR #64 à `1755a3d56990b9456dec54c274341baabfae248f`
(AC 3/3, trois checks verts, review indépendante). La branche de cette recette
intègre ce correctif ; cette cellule demeure `in_progress` tant que le parcours
complet n'est pas prouvé.

Dans ce même projet Linear de sable, `adr create` a créé le témoin explicitement
non décisionnel `[TEST PAT-61 sandbox]` sous `PAT-ADR-0001`, statut `proposed`,
référence initiale `0424d248-6f2d-46c1-92ed-96aa9694b188`. `adr edit` a ajouté
une version du corps (`9468058c-d53e-4255-9464-cd6eebb67588`) ; `adr link-issue`
a lié PAT-77 avec readback de la relation issue (`67a77aae-d2b8-4133-8acd-e4fd78395a65`).
L'index de l'issue contient cette seule ADR et le corps versionné relu gardait alors
le statut `proposed` ; le merge de PAT-78 l'a ensuite changé comme décrit ci-dessus.
Depuis ce sandbox, PAT-ADR-0006 de production reste
`introuvable` malgré l'index local non vide. Dans le dépôt PAT actif,
PAT-ADR-0001 garde son autre référence `ff06e6f5-eae5-4469-bffd-bb1428373ed7`
et son statut `accepted` ; aucun support de production n'a été édité. Ce témoin
qualifie create/edit/lien sur Codex, pas la supersession ou le parcours complet.

Le témoin YouTrack P61Y-2 a ajouté au seul projet P61Y le champ natif `Milestone`
(`189-129`), son bundle propre (`163-67`) et la valeur exacte `PAT61 sandbox v1`
(`164-426`). Le premier essai de création d'issue a reçu HTTP 500 car P61Y ne
possédait pas le champ `Estimate` demandé ; une relecture a confirmé l'absence
d'issue, puis la création sans ce champ facultatif a donné la seule P61Y-2.
`registry update` a publié le mapping avec les coordonnées natives relues. Avant
assignation, `query changelog` et le bridge Ship-iOS retournaient un scope vide ;
après assignation de P61Y-2, tous deux donnent `scope_count=1`,
`counts.unfinished=1` et `release_id=164-426`. Le bridge transporte réellement
`counts`, `categories` et `release_scope`, en plus des champs historiques `count`
et `groups`.

La PR YouTrack #2 a été ouverte via Foundry sur le head `5b1fd05c`, dont le
check `verify` est vert. Sa review indépendante a produit une preuve bloquante
`a78acdc1` : un finding AC2 prétendait à tort que le bridge omettait les champs
ci-dessus, mais une exécution fraîche et le code du bridge le contredisent. Le
finding AC3 est valide : l'AC synthétique exigeait le merge et le classement
post-merge avant le gate de review qui autorise ce merge. Le signal d'escalade
Foundry a retourné `failure_recorded` et aucune autorisation de correction ; le
verdict est conservé, la PR reste ouverte et aucun succès post-merge n'est affirmé.
Une note d'avancement P61Y-2 porte la contre-lecture et ce blocage.
Les lignes 99-110 du bridge citées par le finding AC2 correspondent au checkout
principal ancien et sale, qui n'émet que `count/groups` ; dans le snapshot source
qualifié pour cette recette, ces lignes concernent `_repository_root` et l'émission
finale contient les champs nouveaux. Une seconde note P61Y-2 conserve les deux
chemins exacts : l'erreur de contexte est probable, sans annuler le proof terminal.
Claude Code/Sonnet a ensuite relu depuis ce checkout le binding `youtrack/P61Y`,
P61Y-2 et le même scope release, sans mutation ; son premier essai a été refusé
par les permissions Bash du host, puis la relance avec le répertoire du plugin
autorisé et le seul outil `Bash(python3:*)` a abouti. La lecture confirme une
preuve partielle de l'hôte Claude, pas un deuxième lifecycle.
Une session Claude distincte, avec les deux checkouts de sable autorisés, a ensuite
lu successivement `linear/PAT` et `youtrack/P61Y` avec des cwd explicites ; PAT-74
était `done`/accepté et P61Y-2 `review`/acceptation inconnue. Elle couvre le
changement de dépôt dans un même hôte, toujours sans mutation ni lifecycle complet.

Après autorisation humaine d'une reprise bornée, Foundry a enregistré l'arrêt
`strategy_decision` de P61Y-2 à la génération 1 puis sa reprise
`manual_retry_approved` avec un crédit. L'AC3 a été reformulé pour ne vérifier
que la review indépendante et la CI avant merge ; le contrôle du scope reste une
étape de clôture post-merge. Le README de la branche épingle le code Ship-IOS
réellement utilisé, au SHA `dad098b2a260d07e31de93993343f854cafd4ab4`.
La review du nouveau head `3dc341959c250f7b1db3902a7fb794c716939008`
a passé les trois AC et la qualité (preuve `a2f9fe2108d01d574d2b4a6ce52c4ac0585798230f9dd2bc8ed74069ef0c779f`),
le check `verify` était vert sur ce SHA, et Foundry a mergé la PR #2 sous
`68408d1f426e30284d034727389d0b18ad3fac2f`. P61Y-2 se relit `done`, AC 3/3.
Les relectures fraîches du changelog et du bridge donnent toutes deux
`scope_count=1`, `accepted=0`, `unavailable=1` pour P61Y-2. C'est le résultat
fail-closed documenté par le contrat de release YouTrack : l'état natif `done` et
la PR mergée restent des observations tant que l'adaptateur n'expose pas de reçu
de livraison exact. La note d'issue conserve cette divergence ; aucun `accepted`
post-merge n'est revendiqué et la cellule n'est pas passée.

Le témoin suivant, P61Y-3, cible Claude Code. La création Foundry a produit une seule
issue synthétique (`Submitted`, deux AC), assignée au Milestone de sable. Une session
Claude `start-issue` a épuisé ses 12 tours sans effet ; le coordinateur a ensuite
exécuté la transition Foundry et créé la branche
`fix/p61y-3-pat-61-sandbox-qualifier-le-lifecycle`. Claude `resume-issue` a écrit
un README local encore non commité. Son préflight a établi que le plugin qu'il
charge effectivement est le cache `foundry@patolabs` 0.9.0 au SHA `4d9b0472`,
qui refuse le marqueur `tracker.json` v2 de ce sandbox. Il a lu le projet via une
entrée de registre explicite, ce qui ne satisfait pas la précondition de binding
du parcours normal. Le gestionnaire de plugins répond « already latest 0.9.0 »
malgré le marketplace source à `1755a3d` ; aucune parité de distribution n'est
inférée. Le premier diff README a été préservé puis commité sur `7a1209d`.
Claude en mode source a ouvert la PR #3 via Foundry. La première review a passé
les deux AC mais bloqué une phrase du README devenue fausse dès l'ouverture de
la PR ; la correction sur `5abd4995b3132c0a4f1e250fed8734bfceb7242f` a reçu
une seconde review indépendante favorable (preuve `af943114…`) et le check
`verify` vert. Les deux AC YouTrack ont été cochés sur leur corps exact relu,
puis Claude `foundry:merge-pr` a mergé la PR #3 à
`47bcc26e8404ea90c41efa95c17629c02beb5900` ; P61Y-3 est `done`, AC 2/2.
`P61Y-ADR-0001` se relit toujours `proposed` : l'index projet n'a pas entraîné
d'acceptation ADR. Le scope de release relu contient P61Y-2 et P61Y-3, deux
`unavailable`, zéro `accepted`. Ce parcours source-mode apporte une preuve Claude
partielle mais ne démontre ni un démarrage Claude autonome, ni la parité du cache
installé, ni une livraison Apple ; la cellule reste `blocked`.

Dans P61Y, `adr create` a créé le témoin non décisionnel `P61Y-ADR-0001`
(`P61Y-A-1`, `proposed`) ; `adr edit` a remplacé la phrase version 1 par la
version 2 avec relecture exacte et un seul item dans l'index. `adr link-issue`
vers P61Y-1 a refusé `capability unavailable: youtrack.adr_issue_link` avant
mutation : aucun lien n'est revendiqué et aucune reprise n'a été tentée.

## Exécution d'une cellule

1. Créer un checkout synthétique propre, vérifier son identité Git, installer la même
   distribution Foundry que le SHA retenu, vérifier la chaîne de hooks et relire le binding
   exact. Exécuter le gate PAT-68 sur ce SHA avant le chemin live. Si la PR de sable
   ne contient que son propre marker ou README, donner au reviewer le chemin absolu
   et le SHA du plugin Foundry/Ship-iOS effectivement exécuté ; une lecture d'un
   autre checkout du monorepo ne prouve pas le comportement de cette cellule.
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

Au snapshot `82bf3e5e`, `pytest -q -m tracker_conformance tests` passe (108 tests).
Les lectures fraîches de PAT-42/43/44 et PAT-20/45/46/47/48 indiquent chacune `done`
avec tous leurs AC cochés ; les sondes de binding et de hook ci-dessus revalident une
partie du comportement lié. Ce contrôle de prérequis ne transforme aucune cellule en
`passed`. Une nouvelle lecture du Project GitHub de sable trouve toujours zéro item
dans `ProjectV2.items` contre un item non archivé attaché à P61G-1 depuis l'Issue :
la case GH Projects/Codex demeure `blocked` sans troisième tentative d'écriture.
Une relecture ultérieure a confirmé le même résultat par la liste REST du Project
personnel #9 (vide) et par les variantes GraphQL `archivedStates` et recherche
(vides), tandis que le node de l'item `PVTI_lAHOABroCc4BlTfizg93U2A` garde le bon
Project, la bonne Issue et `isArchived=false`. Dans la même session API, le Project
personnel de qualification #7 liste 14 items : l'accès général à `ProjectV2.items`
fonctionne. La divergence du Project #9 reste inexpliquée, pas résolue.

## Actions live réservées au coordinateur

Le coordinateur doit terminer la parité source/distribution et la vérification de la
chaîne de hooks dans chaque checkout, exécuter les six chemins et leurs tests adverses,
puis effectuer la validation TestFlight humaine de l'application de test. Les dépôts
et projets synthétiques réservés et les deux témoins de PR ci-dessus sont seulement des
étapes de ce parcours ; aucune cellule n'est encore qualifiée comme passée.
