# PAT-61 — recette réelle des six configurations V1

Ce document rend PAT-61 exécutable sans présenter une double de transport comme une
preuve fournisseur. La matrice machine est
[`pat-61-six-configuration-v1.json`](pat-61-six-configuration-v1.json). Les cellules
gardent leur état d'exécution observé ; GitHub Projects × Codex est `passed` sur son
périmètre P61R, tandis que PAT-61 reste `blocked` avec cinq cellules non passées. Le
manifeste ne fabrique aucun receipt.

La recette est une qualification d'intégration, pas un nouveau design d'adaptateur. Les
contraintes existantes de [`tracker-contract.md`](../tracker-contract.md), du contrat de
release [`release-scope.md`](../release-scope.md) et de Ship-iOS restent applicables.
Le gate PAT-68 (`pytest -q -m tracker_conformance tests`) est un préalable de régression
sur ce SHA, mais il n'est jamais une cellule de cette matrice.

## Préconditions et budget

Avant publication, l'opérateur relève le SHA du checkout, les versions affichées de
Foundry et du host, ainsi que le digest du package effectivement chargé. La preuve live
nomme la racine absolue, le SHA et ce digest: au témoin courant, le package
`plugins/foundry` du worktree `pat68-resume` est au SHA
`8a4bbaecb5834f13f69fc9c5a954784020569344`, digest
`426240efcb9d560964d0ffb5df5cdde0d5c9dc5491f416bb6a45d78b2671e117`, 257 fichiers
suivis et version `0.9.0`. Claude Code charge explicitement cette source par
`--plugin-dir`; Codex résout la racine du skill. Ce parcours pré-merge ne prétend
pas prouver un cache installé ni une version 1.0. Une installation propre ou upgrade
par les gestionnaires officiels des deux hôtes, sans édition de cache, avec version
réellement chargée, reste le gate post-publication de PAT-62.
Le checkpoint final intégré est `1ee3f0aeb34256131bdf4f3d4c4c4d66f5e057fe` après
`84cfaa6` puis merge de `origin/main`, sans conflit. Foundry reste 0.9.0 prépublication,
257 fichiers; SHA256(`git archive --format=tar HEAD plugins/foundry`) =
`b4a6b872e056fb7575db9551462cb5e86082d72960f162e5426200902ac225cd`.
Les validations actuelles sur ce HEAD sont 3215 tests publics (10 désélectionnés) et
108 de conformité (3128 désélectionnés). C'est un checkpoint source/conformité: il ne
refait ni les parcours historiques, ni l'installation officielle, ni une CI PAT-61.
`adapter_baseline_sha` identifie seulement le merge de PAT-68 à partir duquel cette
recette a été préparée. Cette précondition est importante ici : au préflight du 1er
octobre 2026, Claude Code et Codex affichaient Foundry 0.9.0, mais des fichiers installés
(`foundry_cli.py` et `ghprojects.py`) différaient de `e313f515`; cela ne qualifie aucun
cache installé, sans empêcher un parcours pré-merge borné au package source exact
effectivement chargé. Le `pre-push` effectif d'un checkout de sable doit appliquer à la fois le garde
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
| ADR déclaré indisponible | Au source propre `f9f420f`, Foundry a lié ADR-3 à P61R-5; l'archivage natif temporaire de ce seul item a laissé P61R-6 `done`/accepté mais P61R-5 `unavailable`, et `query adrs`/`query adr 3` ont refusé avec `AdrIssueUnavailableError` typé | Aucun body ADR supprimé, aucune écriture ADR quand l'index est inconnu, aucun fallback fournisseur; après une erreur interne GitHub, une unique restauration du même item a rétabli exactement ses fields, content et coordonnées |
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

Le préflight Apple initial du 1er octobre 2026 a trouvé une clé App Store Connect locale
référencée par `ASC_KEY_ID`, `ASC_ISSUER_ID` et `ASC_KEY_PATH`, sans afficher son
contenu. Une requête authentifiée en lecture seule sur la liste des apps a reçu
HTTP 403 `FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED` : Apple demande un accord
en vigueur dans App Store Connect Business. Aucune app n'a donc été sélectionnée ou
créée côté fournisseur, aucun workflow Xcode Cloud ni build TestFlight n'a été
déclenché. Le checkout `SouffleApp` possède Fastlane et une identité de production ;
il ne sert pas d'app de test pour cette recette. La frontière Apple restait `blocked`
jusqu'à la régularisation de l'accord par le titulaire du compte, suivie d'une
nouvelle lecture du catalogue avant le choix d'une app synthétique dédiée.

### Reprise Apple et témoin PAT-80 du 1er octobre 2026

Le titulaire a accepté l’accord Apple ; la nouvelle lecture authentifiée du
catalogue répond HTTP 200. Le blocage initial est levé. L’app synthétique
`Foundry PAT61 Hello` (`6818259217`) utilise le bundle
`com.patolabs.foundry.pat61.hello` (`7L87ZWS3J6`), séparé de SouffleApp.

PAT-80 a livré sa préparation dans la PR de sable Linear #4 : head
`ae31cc841f3a3437d34fd3b168aaea5de938d543`, review indépendante AC PASS / QUALITY
mergeable, preuve `7cc0febcf65975a37c2994324671dd17f0ed1fe5b6384db032956ed8d6443495`,
check-run `verify` success et test humain simulateur explicitement validé.
Foundry a mergé sous `78dd8a22b2fb998f7cdeb86488fdf0783b975d01` et clôturé PAT-80
avec AC 3/3. Le bridge V1 Linear relu classe PAT-77, PAT-78 et PAT-80 `accepted`,
zéro `unfinished` et zéro `unavailable`. Les reviews bloquées antérieures restent
conservées : canal alpha des PNG corrigé, puis ancien log fourni au reviewer
remplacé par un rebuild sur le HEAD exact. Le contrôle reproductible vérifie les
18 PNG source et les deux AppIcon compilées sans alpha.

Xcode a créé le produit cloud `1d39219b-d119-43bb-a0af-d8e710f717a7` et son
workflow `Default` (`17A39308-CBD5-48C3-83B8-1FDD1CBD6B56`). Ce workflow a d’abord
été désactivé, puis borné à `main` et réactivé pour un premier build manuel de
découverte du schéma, sans distribution. L’API n’a pas supprimé la condition de
branche pour une valeur `null` : sa relecture a arrêté le premier préflight sans
lancer de build ; une condition explicite `main` a ensuite été relue correctement.
Le build #1 (`fe6420a0-9d83-43f2-aa1c-147d832b3f6f`) est créé ; sa relecture donne
`COMPLETE/SUCCEEDED` et le commit source exact
`78dd8a22b2fb998f7cdeb86488fdf0783b975d01`. Il a terminé à
18:13:45 UTC. Ce build de découverte ne constitue aucune preuve TestFlight.

Le workflow tagué `PAT61 Release` (`cec1b3eb-fb7e-4718-b7d3-1d7d6d5e4220`)
est enregistré et relu : `tagStartCondition` avec préfixe `v`, aucune condition de
branche, action `ARCHIVE`, schéma `FoundryHello`, audience
`APP_STORE_ELIGIBLE`. L’interface relue montre la post-action TestFlight interne
limitée au groupe `PAT61 qualification interne`
(`86d551bd-ea34-4c7b-9523-e531d25ae9ef`), zéro membre. L’API du groupe renvoie
`isInternalGroup=true` et `publicLinkEnabled=null` ; cette valeur n’est pas
requalifiée en `false`. Le workflow `Default` est désactivé après le build de
découverte. Aucun tag de release n’a été poussé.

Aucune déclaration de chiffrement, soumission App Store ni validation TestFlight
sur appareil réel n’est revendiquée. La frontière Apple est `in_progress` et
aucune cellule E2E ne devient `passed` sur la base de cette préparation.

## Matrice de preuve

| Cellule | Ressource à réserver | État de cette livraison | Preuves réutilisables, avec limite |
| --- | --- | --- | --- |
| YouTrack × Claude Code | dépôt et projet YouTrack synthétiques actifs | `in_progress` | P61Y-6/PR #5 est mergée `98bd2de`, `done`/AC 2/2/acceptée; ADR-2 est `proposed` V2. Epic P61Y-7 lie P61Y-6 dépendant de P61Y-4, et P61Y-5 lie P61Y-4; les deux attendent le verdict humain `accepted`. Le scope frais 4/6 est `accepted`; la capacité native de fermeture reste `unavailable`. L'arrêt max_turns est post-effets, pas un échec d'étape. |
| YouTrack × Codex | P61Y et dépôt privé de sable | `in_progress` | Le milestone #4 a maintenant le scope frais P61Y-4 seul, `accepted` 2/2, `unfinished=0`, `unavailable=0`; la capacité native de fermeture est `unavailable`, donc aucun état natif fermé n'est inventé. P61Y-2/P61Y-3 restent historiques `unavailable`. L'Epic P61Y-5 est backlog avec P61Y-4 enfant `done`/accepté; le lien existant P61Y-6→P61Y-4 est rejoué sans écriture et les deux issues restent byte-identiques. Verdict humain/closure manquent. |
| Linear × Claude Code | projet PAT synthétique et dépôt privé de sable | `in_progress` | Les huit BLOCK historiques sont conservés; Foundry a mergé PR #66 après 3 checks `success` sur `75c0116…ad53`, AC proof-projected 3/3, SHA `bc25c344…b36b`, PAT-85 `done`. L'intégration de `main` est terminée sans conflit au HEAD `1ee3f0a`; les clôtures Epic humaines restent pendantes; aucun PASS. |
| Linear × Codex | projet PAT synthétique et dépôt privé de sable | `in_progress` | Les receipts antérieurs et les huit BLOCK restent conservés; PAT-85 est `done` après merge Foundry et l'intégration de `main` est terminée sans conflit au HEAD `1ee3f0a`. L'Epic PAT-83 est backlog (parent PAT-77), verdict humain demandé sans réponse, donc ni closure ni preuve terminale; aucun PASS. |
| GitHub Projects × Claude Code | P61R, dépôt privé et Project personnel privé #10 | `in_progress` | ADR-8 expérimental est `proposed` V2. L'Epic P61R-9, réconcilié après `partial item_readback`, est parent de P61R-6, unique enfant requis, sans dépendance requise; groom et bridge frais relisent le scope 6 `accepted`. La clôture humaine reste pendante. Le marker Foundry du sandbox est intentionnellement non commité; l'Issue native 6 est ouverte mais son état Project est `done` avec reçu qualifié. |
| GitHub Projects × Codex | P61R, dépôt privé `patobiskoto/foundry-v1-pat61-ghprojects-recovery-sandbox` et Project personnel privé #10 | `passed` | Les six étapes P61R sont couvertes: binding; ADR-3 V1→V2 et refus ADR indisponible/restauration exacte; Epic P61R-4 avec enfant, dépendance P61R-5/P61R-6 et groom; P61R-1 start/reprise/PR/review/CI/merge; clôture Epic relue alignée; scope P61R-1 et bridge Ship-iOS `accepted`. La cellule est qualifiée sur ce périmètre; PAT-61 global reste `blocked` par les cinq autres cellules. |

GHQUAL et P64G peuvent être relus comme ressources historiques uniquement. P64Q et
l'ancien binding FOUNDRY sont archivés : ils ne sont pas réactivés et ne constituent pas
un raccourci. Au source propre `14b4f1802386806ebf2cc0ea1e47fff280fe3efa`, le package
Foundry 0.9.0 compte 257 fichiers; son SHA256 `git archive` est `fdb1d2ac…`, algorithme
explicitement distinct de l'agrégat historique `8a`. Claude 2.1.267 a confirmé sa racine
inline. Le lecteur Claude frais a relu une seule fois changelog et bridge complets:
scope Linear PAT-77/PAT-78/PAT-80/PAT-81 tous `accepted`, sans tag ni effet Apple; coût
listé 0,128946 USD, inclus dans 4,154751 USD sur trois invocations, ni facture ni quota.
Les 108 tests de conformité passent (3051 désélectionnés); les 45 tours/2,9773205 USD de
la phase native et la continuation tronquée ne sont pas une preuve du bridge. Les dépôts privés de sable `foundry-v1-pat61-linear-sandbox`,
`foundry-v1-pat61-youtrack-sandbox` et `foundry-v1-pat61-ghprojects-sandbox` sont
désormais enregistrés avec leurs bindings séparés PAT, P61Y et P61G. Ils ne modifient
pas le binding du dépôt `patolabs-plugins`.

La revalidation finale des prérequis relit PAT-42, PAT-43, PAT-44, PAT-20, PAT-45,
PAT-46, PAT-47, PAT-48 et PAT-68, tous mergés/acceptés avec AC complètes. Toute dérive
de projection native observée est conservée explicitement, jamais normalisée pour
fabriquer une preuve.

Le préflight live du 1er octobre 2026 a confirmé deux témoins Codex limités :
PAT-74 a été mergé dans le dépôt Linear de sable (PR #1, head `f52efdbc`, merge
`855dd887`, AC 2/2) et P61Y-1 dans le dépôt YouTrack de sable (PR #1, head
`b4724103`, merge `afe441dc`, AC 2/2). Le second a nécessité l'attachement au seul
projet P61Y du prototype YouTrack `GitHub PR` déjà existant. Ces témoins ne couvrent
ni ADR/Epic, ni tous les adversaires, ni le chemin Apple : les cellules restent
`in_progress`. Historiquement, le premier create P61G-1 a laissé une Issue et un item
consultable depuis l'Issue, mais `Project.items` reste vide ; le rejeu borné a échoué
une seconde fois à `partial_create:item_readback`. Son intention locale est conservée,
sans nouveau retry ni assertion de succès GitHub Projects.

Une relecture bilatérale du lien de dépendance Linear dans la cellule Codex montre
PAT-75 (`ready`, AC 0/1) avec `depends-on` vers PAT-74, et PAT-74 (`done`, AC 2/2)
avec le lien inverse `blocks` vers PAT-75. Ce témoin prouve la relation native et
la projection dans les deux sens, mais ne constitue ni une clôture de PAT-75 ni un
graphe Epic qualifié.

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
`passed`. Avant la reprise autorisée, une nouvelle lecture du Project GitHub de sable
trouvait toujours zéro item
dans `ProjectV2.items` contre un item non archivé attaché à P61G-1 depuis l'Issue :
la case GH Projects/Codex demeurait `blocked` sans troisième tentative d'écriture.
Une relecture ultérieure a confirmé le même résultat par la liste REST du Project
personnel #9 (vide) et par les variantes GraphQL `archivedStates` et recherche
(vides), tandis que le node de l'item `PVTI_lAHOABroCc4BlTfizg93U2A` garde le bon
Project, la bonne Issue et `isArchived=false`. Dans la même session API, le Project
personnel de qualification #7 liste 14 items : l'accès général à `ProjectV2.items`
fonctionne. La divergence du Project #9 reste inexpliquée, pas résolue. La relecture
du jour maintenait `totalCount=0`, l'item non archivé et l'Issue #1 `OPEN`. Le budget
de cette tentative était épuisé : une décision humaine sur un unique sandbox de reprise
isolé était requise avant tout nouvel essai.

## Actions live réservées au coordinateur

Le coordinateur doit terminer la parité source/distribution et la vérification de la
chaîne de hooks dans chaque checkout, puis exécuter les six chemins et leurs tests
adverses. La validation TestFlight humaine commune est déjà consignée; les dépôts et
projets synthétiques réservés et les témoins de PR ci-dessus restent seulement des
étapes de ce parcours. Ce constat précède les relectures P61R ultérieures; GitHub
Projects × Codex est désormais qualifié `passed` sur son périmètre, sans débloquer PAT-61.


## Suivi Apple après PAT-80 — PAT-81

Le mainteneur a explicitement confirmé la déclaration d'export pour l'app locale sans
cryptographie. PAT-81 ajoute `ITSAppUsesNonExemptEncryption = NO` dans XcodeGen et
le projet généré ; le build simulateur du HEAD `204d807872e3c0977a135007a18f296cbc60b537`
réussit et son Info.plist contient le booléen `false`. Review indépendante favorable
(preuve `4d149c60…`), CI `verify` verte sur ce HEAD, puis Foundry merge sandbox PR #5
à `ac36ccac6916b657932bf29949a0227ac43b742c`, PAT-81 done AC 2/2. L'interface reste
celle validée humainement pour PAT-80.

Le helper Ship-iOS a publié uniquement `v1.0` sur ce SHA mergé. Apple relit le run
Release #2 `92286db6-7b83-45ca-96f8-ba45c814d8e7`, `GIT_REF_CHANGE`, sourceCommit
identique, initialement `PENDING`. Aucun succès d'archive, upload TestFlight ou test
sur appareil physique n'est encore revendiqué. Le groupe interne demeure dédié ;
la lecture des testeurs via la clé API retourne 403, sans mutation de testeur.
La baseline Fastlane affiche sa valeur par défaut 1 alors que la liste Apple des
builds est vide : cette valeur n'est pas un build observé.


Relecture finale du même run : `COMPLETE/SUCCEEDED`, terminé le 1er octobre 2026
à 18:44:07 UTC ; archive et distribution interne chacune `SUCCEEDED`. Le build
lié `a3426daa-dfb8-4cbf-b3b6-7efd21b1ea64`, version `1.0 (2)`, est `VALID`,
`APP_STORE_ELIGIBLE`, `usesNonExemptEncryption=false`, upload à 18:42:00 UTC.
L'interface native App Store Connect relit ce build dans le groupe interne dédié,
une invitation et un seul testeur : le titulaire existant du compte. Aucun nouveau
compte, permission administrateur, groupe externe ou soumission publique.
Le test physique installation/Hello World/fermeture complète/relance est demandé
au mainteneur et reste en attente ; aucun PASS global ou de cellule n'en est déduit.


Le mainteneur a ensuite répondu « c'est bon » à la procédure ciblée sur le build
TestFlight `1.0 (2)` : installation sur iPhone physique, affichage Hello World,
fermeture complète et relance sans crash. Le gate bêta du chemin Apple partagé
est donc satisfait. Cela ne vaut ni soumission App Store ni PASS des six cellules,
dont les étapes tracker/host restantes doivent toujours être démontrées.


Après validation iPhone, le blocage GitHub #9 persiste : Foundry refuse P61G-1
avec `IssueUnavailableError`, l'API relit le Project à zéro item et le node direct
non archivé inchangé ; l'interface GitHub authentifiée montre également une table
de projet vide sans filtre. Cette relecture ne répète aucune écriture et ne remet
pas à zéro le budget de reprises. Aucune réparation destructive ou recréation.

## Reprise GitHub Projects isolée autorisée

L'autorisation humaine alors requise a couvert une seule reprise isolée, sans modifier
le Project #9, son dépôt, ses audits ni son budget historique. Le sandbox privé
`patobiskoto/foundry-v1-pat61-ghprojects-recovery-sandbox` utilise le binding P61R et
le Project personnel privé #10 (`PVT_kwHOABroCc4BlYLD`). Le runtime source de ce
témoin est exactement le HEAD producteur
`662df7fc66fafd4e5cc5b53c10aad63d9eea25a1`, qui contient PAT-82 mergé à
`f7fb4e1`; ce constat ne revendique aucune parité avec une distribution installée.

Le premier `create-issue` a retourné `partial_create:item_readback` pour P61R-1.
Les relectures GraphQL et REST ont ensuite trouvé de manière univoque l'Issue native
et son item existant. Le rejeu Foundry de même intention n'a complété que les champs,
sans créer de seconde Issue ni de second item. Foundry a ensuite exécuté start,
open-pr, review, CI et merge : la PR #2 a le head
`67c5db9caec373d50afec7e2052b206a4f3b0c42`, la base
`adb89e430a245f29d59df8eeb56f5daf7c7731fc` et le merge
`43d31bfd5b32649b556ce24fdaa1ac3de8a362e3`. La review de génération 1 est
`mergeable`, ses deux AC sont `pass` et sa preuve est
`471bb8495cd7ef40542ebfafaa8f8824aa0c3b4e2bdd0cacdfea49d4e9873ae7`.
Le check `verify-witness` est `success` sur le head exact ; les statuts legacy sont
vides.

Après merge, P61R-1 est relu `done`, avec états natif et normalisé alignés et
acceptation `ghprojects-acceptance-proof` 2/2. Le Project #10 relit un item unique,
non archivé, avec `hasNextPage=false`, et la liste REST relit le même item. L'Issue
GitHub sous-jacente reste `OPEN` : l'état Foundry `done` est ici le champ de projet,
sans divergence cachée ni autorité de fermeture manuelle de l'Issue. La cellule
GitHub Projects × Codex était donc `in_progress` pour ce chemin de livraison, jamais
`passed` dans cet instantané initial : ADR, Epic, parcours hôte complet, cas adverses,
parité installée et bridge Ship-iOS propre à la cellule restaient alors incomplets.

### Roundtrip ADR et Epic P61R après la livraison

Au source exécuté `f743f2e`, le roundtrip ADR expérimental a été borné au seul
P61R. La création initiale de P61R-ADR-0003 s'est arrêtée sur
`candidate_not_visible`, puis son rejeu de même intention sur
`item_effect_unknown`. Après autorisation humaine explicite d'une unique reprise
supplémentaire de cette même intention, Foundry a créé l'ADR `proposed`, ref `3`.
L'édition nominale V1→V2 a été relue exactement; les commentaires natifs
`5945946498` et `5945954397` préservent les deux versions et leur chaîne. L'ADR porte
le label `foundry:adr`, est exclue du backlog, et ne constitue aucune décision
d'architecture produit ni acceptation. Elle reste `proposed`.

La création nominale de l'Epic P61R-4 (native `5673158090`) a d'abord retourné
`partial_create:item_readback`; les écritures se sont arrêtées comme annoncé. À la
reprise demandée par l'utilisateur, la candidate et l'item natifs ont été relus
exactement, puis Foundry a complété les champs du même Epic, sans doublon. Foundry a
créé le lien `parent-of` P61R-4 → P61R-1; les relectures fraîches de l'Epic et de
l'enfant confirment le lien réciproque, P61R-1 `done`, AC 2/2, acceptation qualifiée
et PR #2 mergée à `43d31bfd5b32649b556ce24fdaa1ac3de8a362e3`.

Au source exécuté `f56776486ccba307b3a3e71f26f201db28108f29`, le verdict humain
explicite `accepted` a autorisé `close-epic P61R-4 --human-verdict=accepted`.
La relecture fraîche donne l'Epic `done`, états natif et normalisé alignés; son seul
commentaire natif est l'audit append-only `github:epic:9ba37bf81a564a36cc437dde2d78409bb6e43aec900b5af48a5306d319f6f088`
(`5963150172`). Il établit l'enfant unique P61R-1, `done`, AC 2/2 et `accepted`,
ainsi que l'absence de dépendance. L'Epic reste type `Epic`, AC 0/2 et
`acceptance_status=null`: sa clôture non-code repose sur le verdict humain et cet
audit, sans cocher artificiellement ses AC ni lui attribuer une preuve de code.

Le backlog frais de l'Epic avant sa clôture est `Type=Epic`, AC 0/2. Le grooming a parcouru une page
complète (2 éléments retournés sur 2, sans troncature), sans dépendance ni doublon et
sans correction proposée. Le Project #9 historique, son budget de reprises épuisé et
l'exception humaine déjà consommée restent inchangés; ce constat ne réinitialise aucun
budget général.

### Scope de release P61R et bridge Ship-iOS

Le registre Foundry a conservé les coordonnées existantes du sandbox P61R
(owner, numéro et `canonical_repo`) et ajouté seulement le mapping `release_ids`; son
digest est `sha256:c3324282086529bcb24a024173e978ab2fe9b54e26d70c52d441ea90c03a6869`.
Au snapshot `f567764`, le marqueur `.foundry/tracker.json` généré dans le sandbox était
volontairement local, non commité et non poussé. Il a ensuite été commité dans
`b06af3a` et intégré à la PR #7. L'opérateur a créé le milestone natif de dépôt
`PAT61 sandbox v1` (#1, id `18273978`), puis a assigné uniquement P61R-1. Sa relecture
confirme le changement ciblé de milestone et le changement automatique de `updated_at`;
toutes les autres propriétés non ciblées sont préservées.

Au même source `f567764`, `query changelog` et le bridge Ship-iOS en mode
`foundry-v1` relisent le même scope fermé: `scope_count=1`, `accepted=1`,
`deviated=0`, `unfinished=0`, `unavailable=0`, avec P61R-1 seul. Après ces
préconditions, l'opérateur a fermé uniquement ce milestone synthétique; les deux
relectures fraîches donnent `native_state=closed` et les mêmes coordonnées. Cette
clôture ne crée ni tag, ni publication d'application, ni nouvel effet Apple; elle ne
réutilise la preuve beta Apple historique que dans sa frontière déjà déclarée.

Le bridge P61R peuplé et accepté est observé dans cet instantané de `f567764`. Les
preuves de clôture et de scope restent valides pour le moment où elles ont été relues;
elles ne garantissent pas une projection Epic qui changerait ensuite. La conformité sur
`f567764` a passé 108 tests, 3051 désélectionnés : c'est un prérequis déterministe,
pas un receipt fournisseur ni une cellule passée.

### Divergence du graphe après clôture

Au source `41d6866b48780fd3cb3bf386b4ecac66031b9c63`, le coordinateur a créé la
fixture synthétique P61R-5 (native `5684847992`, item
`PVTI_lAHOABroCc4BlYLDzg-PUMA`) par Foundry. Le premier create a retourné
`partial_create:item_readback`; une relecture native unique, puis une reprise de même
intention, ont terminé la création. Foundry a ensuite créé `P61R-5 depends-on P61R-1`.
La relecture fraîche de P61R-5 ne diffère que par `links`; le rejeu common-write n'a
observé aucune écriture (`REST_write=0`, `GraphQL_mutation=0`).

Ce lien réciproque modifie l'instantané de l'enfant de l'Epic P61R-4 déjà clos.
`query issue P61R-1` avec son Epic, `query profile groom` et `query issue P61R-4`
refusent donc `TrackerConflictError: GitHub Epic graph changed after closure`.
Aucun audit n'a été remplacé, aucun état forcé et le lien n'a pas été retiré. Le garde
fail-closed est le comportement attendu, sans bug de contrat ni redesign ADR. Foundry
ne possède pas de commande unlink. Le mainteneur a autorisé le cleanup natif ciblé de
ce seul lien P61R-5 → P61R-1 (native Issue id `5668442786`). Les lectures REST avant
et après conservent toutes les propriétés hors lien et donnent `blocked_by` et
`blocking` vides. La relecture Foundry fraîche restaure P61R-4 `done`, états natif et
normalisé `done`, projection `aligned` et son unique audit original intact. Le groom
relit trois issues (une active, deux historiques), sans troncature ni pagination.
P61R-5 reste `backlog`, AC 0/2, sans `accepted`. GitHub Projects × Codex revient donc
à `in_progress`, jamais `passed`: un graphe de dépendances non vide, le parcours hôte
complet, la parité installée et les adversaires restent à qualifier.

### Témoins supplémentaires GitHub Projects

Au SHA `8a4bbaecb5834f13f69fc9c5a954784020569344`, Claude Code 2.1.267 a chargé le
package source décrit en précondition via `--plugin-dir`, puis exécuté les parcours
Foundry `resume`, `start`, `open-pr` et `merge-pr`, avec Eiffel et Maigret. P61R-6/PR
#7, basée sur `43d31bfd5b32649b556ce24fdaa1ac3de8a362e3`, a d'abord été bloquée en
qualité: le README disait le marker inchangé alors que le commit `b06af3a` le contenait.
La correction README seule, head `afb8f906b43baf55e0b2fc8fc59ad443ec68749e`, a reçu la
review fraîche génération 2, AC `pass`, qualité `mergeable`, preuve
`97386b4f642f65f17124b838c16fd2d1ee7e54dc104b5877153acb22d4dffd07`; `verify-witness`
est `success`, les statuts legacy sont vides, puis Foundry a mergé
`a523f170c5ee553299f02a0a40a22799f77801ab`. La relecture post-merge donne P61R-6
`done`/aligné/accepté AC 2/2. ADR-3 reste `proposed` et aucune frame ne la cite. Une
reprise `partial_create` de même intention et une correction review consomment le budget
2/2. Les quatre invocations listent 0,6163695 USD (préflight), 1,9711668 USD
(delivery), 1,9255275 USD (continuation review) et 3,054777 USD (dernière invocation de
correction), soit 7,5678408 USD au total; ce ne sont ni une facture ni un quota. Le
premier hôte a atteint 35 tours, puis la continuation review a été bloquée; la correction
s'est terminée `error_max_budget_usd` après merge et reçu fournisseur. Le checkout local
`main` à `b06af3a` a été conservé par renommage
`local-p61r-release-binding-b06af3a`; le nouveau `main` suit `origin/main` et la feature
est conservée.

Après ce nettoyage, Codex a modifié le body de P61R-5 pour cibler P61R-6 plutôt que
P61R-1, puis a créé `P61R-5 depends-on P61R-6` et son lien réciproque
`P61R-6 blocks P61R-5`. Le rejeu strict est identique avant/après et n'observe aucune
écriture (`REST_write=0`, `GraphQL_mutation=0`). Le groom relit quatre éléments, dont le
composant connecté P61R-5/P61R-6 et trois historiques (P61R-1, P61R-4, P61R-6), sans
troncature, next page, cycle ni `unavailable`. Epic P61R-4 reste inchangé et aligné.
Cette preuve de graphe Codex, prise isolément, ne transforme aucune cellule en `passed`;
l'installation ou l'upgrade officiel des deux hôtes reste le gate PAT-62 post-publication. Le scope YouTrack
historique `unavailable` reste tel quel; un nouveau scope ne pourra être testé plus tard
qu'avec des receipts qualifiés.

Au source propre `f9f420f`, Foundry a relié ADR-3 à P61R-5, puis l'opérateur a archivé
temporairement le seul item natif `PVTI_lAHOABroCc4BlYLDzg-PUMA` après vérification des
coordonnées privées et de ses sept champs. P61R-6 reste `done`/accepté, mais P61R-5 est
alors `unavailable` et l'index ADR devient exactement
`adr_issue_unavailable` pour ADR-3 → P61R-5. `query adrs` et `query adr 3` refusent
avec `AdrIssueUnavailableError`; aucun body ADR n'est supprimé ni écrit lorsque la
contrainte est inconnue. La première restauration a reçu une erreur interne GitHub;
après relecture archivée exacte, une unique restauration du même item a réussi et a
rétabli à l'identique fields, content et coordonnées (`restored_exactly=true`). Aucun
champ n'a été forcé, aucune preuve fabriquée, aucun fallback fournisseur n'est invoqué.
La conformité du source `f9f420f` donne 108 tests, 3051 désélectionnés; ce test adverse
pris isolément ne rend aucune cellule `passed`.

### Audit des six étapes requises

Pour GitHub Projects × Codex, les six étapes du `required_journey` sont couvertes dans
la borne P61R: binding frais; création et évolution V1→V2 d'ADR-3; Epic P61R-4 avec
enfant, dépendance non vide P61R-5/P61R-6 et groom sans cycle; start/reprise/PR/review/CI
et merge P61R-1; clôture Epic et projection relue alignée après cleanup; scope de release
P61R-1 et bridge Ship-iOS relus acceptés. Le refus ADR indisponible et la restauration
exacte complètent le cas adverse déclaré. L'hôte Codex relevé le 3 octobre est
l'exécuteur embarqué ChatGPT `.../CodexCLI.app/Contents/MacOS/codex` 0.159.2 (app
26.928.31416, build 12553), et non le binaire PATH 0.155.1; le plugin source 0.9.0 est
attesté au SHA `f9f420f`. Aucun test de supersession n'est requis par l'évolution ADR
observée, ni parité installée avant publication, ni six TestFlight par
cellule: l'installation officielle reste PAT-62 post-publication et Apple est partagé
dans sa borne autorisée. Ces six éléments et leurs coordonnées existantes qualifient
GitHub Projects × Codex `passed`; cette classification ne crée aucun receipt. Le bilan
global PAT-61 reste `blocked` (AC 0/6) tant que les cinq autres cellules ne passent pas.

Les gaps actuels des autres cellules restent: YouTrack × Claude Code manque un parcours
autonome complet; un nouveau scope de receipts frais qualifiés, dont P61Y-4, peut éviter
les historiques `unavailable` sans backfill. YouTrack × Codex a la même prochaine étape de
scope frais, ainsi que les étapes Epic/parcours complet absentes. Linear × Claude Code manque les étapes ADR/Epic et le parcours
de release complet; Linear × Codex manque encore l'Epic, ses enfants/dépendances/groom et
le parcours complet. Aucun de ces constats n'ajoute de gate ou ne transforme un reçu
historique en preuve terminale.


Au HEAD `0f175af`, la suite de conformité passe : 108 tests, 3031 deselected.
Le diagnostic YouTrack relit P61Y-2 et P61Y-3, Done/AC complètes/PR mergées, mais
le code classe systématiquement une livraison terminale sans reçu qualifié comme
`unavailable`. Ce comportement est explicitement testé et ne sera pas contourné.
L'intake, après lecture des trois pages du même snapshot, crée PAT-82 et propose
PAT-ADR-0009 pour qualifier un reçu décisionnel dans les commentaires natifs
YouTrack. Aucun code n'est changé avant acceptation explicite, aucun reçu
historique n'est fabriqué. Les deux cellules YouTrack deviennent `blocked`.
PAT-61 reste in-progress ; la frontière Apple validée n'efface pas ces blocages.

## Reprise après PAT-82

PAT-ADR-0009 est désormais `accepted`, et PAT-82 est intégré au SHA
`f7fb4e14f272de78d7f848b3c23e4a963a623087`. Son témoin synthétique P61Y-4 a été
mergé par Foundry (PR #4, SHA `7ff9453a9c067d290333dcf000fa8d0d60f07ac9`) avec
un reçu de livraison YouTrack frais : les relectures du changelog et du bridge
convergent vers `accepted=1`, `unfinished=0`, `unavailable=2` pour trois items.
PAT-82 est `done` (4/4) : la review finale `54ce063d8ddd5c67a83663ab8ecfcabd21806bf91334613324a40ad700544f38`,
trois CI réussies et la validation humaine ont précédé son merge. La correction
`3b45f76` conserve le refus de tout backfill terminal historique avant intention et
POST.
Cette observation prouve la nouvelle capacité pour P61Y-4 seulement. P61Y-2 et
P61Y-3 restent `unavailable`, conformément au refus explicite de backfill des
livraisons historiques. Le scope P61Y ne peut donc toujours pas être clôturé.

La capacité de reçu n'est plus un blocage architectural ou d'implémentation de
PAT-61. Les cellules YouTrack restent toutefois `blocked` faute de parcours complets
et, pour Claude Code, faute de parité de distribution installée et de démarrage
autonome. Les autres cellules conservent leurs états observés; aucune des six ne
devient `passed`.
