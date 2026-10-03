# PAT-61 — bilan final de qualification pour revue indépendante

**Verdict proposé : PASS avec limites documentées.** Les 36 étapes requises ont des observations réelles identifiées. Ce verdict concerne la recette source bornée ; PAT-61 reste `in-progress`, AC 0/9 au readback antérieur à cette revue. Il ne vaut ni livraison, ni CI PAT-61, ni release V1.

Le [manifeste](pat-61-six-configuration-v1.json) porte les coordonnées détaillées, la classification de chaque étape et les SHA256 des fichiers sauvegardés. Les `partial_evidence` sont conservés verbatim comme histoire. Les récits de blocage antérieurs ne décrivent pas la classification courante. L’[observation native intégrée](pat-61-post-integration-observation.json) conserve la copie exacte de `/tmp/pat61-final-native-observation.json`. Le [complément ADR natif](pat-61-native-adr-evolution-observation.json) ajoute la copie exacte de `/tmp/pat61-v3-native-observation.json` ; aucun de ces fichiers n’est un receipt.

## Identité et périmètre

Checkpoint source : `4559570ee7f8882a12f2d60236db1d79870c6bcd`, racine `/Users/pato/.codex/worktrees/pat68-resume/patolabs-plugins/plugins/foundry`, Foundry **0.9.0**, archive SHA256 `e1a8a40b5dda6ed4e4abef74c74de05b48e0a239a6ad34b4c7fd2c1a9b26b5b1`. Cela ne prouve pas une installation 1.0. Les SHA producteurs antérieurs restent attachés à leurs étapes.

Claude historique : 2.1.267, plugin source explicite ; session intégrée : 2.1.285, `claude.ai` / `firstParty` / Pro, parent demandé `sonnet-5.5`/`medium`, transmis `claude-sonnet-5-5`/`medium`, modèle assistant et modelUsage observés `claude-sonnet-5-5`. L’effort runtime non exposé reste `unknown`. Codex relevé le 3 octobre : exécuteur embarqué ChatGPT 0.159.2, app 26.928.31416/build 12553 ; le PATH 0.155.1 est un autre binaire. Ce relevé ne réécrit pas les versions historiques non capturées.

Les deux hôtes réutilisent les dépôts privés synthétiques déjà créés ; chaque parcours sélectionne son binding. Les clôtures humaines opérées par le coordinateur et le chemin Apple commun sont identifiés comme tels, jamais comme une mutation autonome de l’autre hôte.

| Tracker | Dépôt privé | Projet |
| --- | --- | --- |
| youtrack | `github.com/patobiskoto/foundry-v1-pat61-youtrack-sandbox` | `P61Y` / `0-10` |
| linear | `github.com/patobiskoto/foundry-v1-pat61-linear-sandbox` | `PAT` / `d3d412b6-1327-4bba-905b-01e4bb29797f` |
| ghprojects | `github.com/patobiskoto/foundry-v1-pat61-ghprojects-recovery-sandbox` | `P61R` / `PVT_kwHOABroCc4BlYLD` |

## Matrice des 36 étapes

B = binding neuf/sélection ; A = ADR création/évolution ; G = Epic/enfants/dépendances/groom ; D = start/reprise/PR/revue/CI/merge ; C = clôture/projection ; R = scope/changelog/bridge Ship-iOS. `passed` désigne une étape étayée dans le parcours composite décrit, sans réétiqueter son exécution historique au SHA intégré.

| Cellule | B | A | G | D | C | R |
| --- | --- | --- | --- | --- | --- | --- |
| youtrack-claude | passed | passed | passed | passed | passed | passed |
| youtrack-codex | passed | passed | passed | passed | passed | passed |
| linear-claude | passed | passed | passed | passed | passed | passed |
| linear-codex | passed | passed | passed | passed | passed | passed |
| ghprojects-claude | passed | passed | passed | passed | passed | passed |
| ghprojects-codex | passed | passed | passed | passed | passed | passed |

La ligne Linear × Claude conserve l’attribution historique : **création V1 native Claude, récupération/évolution V1→V2 PAT-85 par coordinateur, puis relecture native du V2 exact à 4559570**. Le complément explicitement autorisé ajoute maintenant **l’évolution native Claude V2→V3 et sa relecture exacte à source 4f142e5**. Le Bash natif a appelé une fois le helper exécutant le CLI normal Foundry `adr edit PAT-ADR-0002` ; seul le marqueur change, avec statut `proposed`, titre, relations, projet et anciens documents conservés. La nouvelle évolution est attribuée à Claude ; la récupération antérieure garde son producteur. Le PASS reste proposé pour une nouvelle revue indépendante des neuf AC.

| Cellule / étape | Coordonnées réelles et attribution | Source / host |
| --- | --- | --- |
| youtrack-claude / B | github.com/patobiskoto/foundry-v1-pat61-youtrack-sandbox; project {'id': '0-10', 'key': 'P61Y'}; configuration sha256:d65a3a4572f9b34f6080896810318b5ae507eebf98d7903d232872b33372876c | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| youtrack-claude / A | P61Y-ADR-0002 / P61Y-A-2 / proposed V1→V2 | dacb80232ef9bc1c09d5718bae3528e885ea63b4 / Claude Code 2.1.267; reread 4559570 |
| youtrack-claude / G | P61Y-7 -> P61Y-6; P61Y-6 depends-on P61Y-4 | dacb802 Claude graph; 1ee3f0aeb34256131bdf4f3d4c4c4d66f5e057fe Codex dependency replay; reread 4559570 |
| youtrack-claude / D | P61Y-6 / PR #5 / head 19b26a48c1542dac99ff31808639fbbd7a8d7095 / merge 98bd2deff1a0bbe1b1cecb3d2201d8f175b7abf3 / proof 5152410b4c0ecc5720a605f8346e9d63ecba48665d75e8eceb9085015b325659 / done accepted AC 2/2 | dacb80232ef9bc1c09d5718bae3528e885ea63b4 / Claude 2.1.267 native parent+Eiffel/Maigret |
| youtrack-claude / C | P61Y-7 audit foundry-epic-closure.v1:f3f74a1b6e819b099132c6826040d9d163791443182287744a2bace8d4f4cc27 | actual operator human accepted closure after PAT-16; final authenticated native Claude read 4559570 |
| youtrack-claude / R | PAT61 fresh receipt qualification: P61Y-4/P61Y-6 accepted only | fresh receipts after PAT82; per-host historical bridge; final Claude integration4559570 |
| youtrack-codex / B | github.com/patobiskoto/foundry-v1-pat61-youtrack-sandbox; project {'id': '0-10', 'key': 'P61Y'}; configuration sha256:d65a3a4572f9b34f6080896810318b5ae507eebf98d7903d232872b33372876c | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| youtrack-codex / A | P61Y-ADR-0001 / P61Y-A-1 / proposed V1→V2 | historical Codex source witness documented in recipe; not relabelled4559570 |
| youtrack-codex / G | P61Y-5 -> P61Y-4; P61Y-6 depends-on P61Y-4 | dacb802 Claude graph; 1ee3f0aeb34256131bdf4f3d4c4c4d66f5e057fe Codex dependency replay; reread 4559570 |
| youtrack-codex / D | P61Y-1 PR1 mergeafe441dcccc2ddd040c524b16f329fe2f772e533; P61Y-2 PR2 corrected head3dc341959c250f7b1db3902a7fb794c716939008 merge68408d1f426e30284d034727389d0b18ad3fac2f; P61Y-4 PR4 merge7ff9453a9c067d290333dcf000fa8d0d60f07ac9 | historical Codex nominal P61Y-1/2 plus fresh PAT82 producer receipt witness |
| youtrack-codex / C | P61Y-5 audit foundry-epic-closure.v1:0a6436cb01d7ed44cf5555a400de59d7ca62c01ba4e5ac0c5213e8712ab3d100 | actual operator human accepted closure after PAT-16; final authenticated native Claude read 4559570 |
| youtrack-codex / R | PAT61 fresh receipt qualification: P61Y-4/P61Y-6 accepted only | fresh receipts after PAT82; per-host historical bridge; final Claude integration4559570 |
| linear-claude / B | github.com/patobiskoto/foundry-v1-pat61-linear-sandbox; project {'id': 'd3d412b6-1327-4bba-905b-01e4bb29797f', 'key': 'PAT'}; configuration sha256:e45ffccbcd795f39512acae828c2f8c84fff0b73d2faf418b5f4fddf7d8ee6c7 | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| linear-claude / A | PAT-ADR-0002 V0=95b3ab9e-72ee-454b-8178-0c6e014f0eb0; V1=d590fe2b-eb78-4297-9391-5be4e68d34f7; proposed V2→V3 ref b6a6f4aa-47a2-40db-a3b0-1cbb9f419128, séquence1→2 | Claude native create14b4f1802386806ebf2cc0ea1e47fff280fe3efa; PAT-85 targeted recovery/evolution; Claude native integration read 4559570; authorized native Claude edit/reread4f142e5 |
| linear-claude / G | PAT-84 -> PAT-78; PAT-78 depends-onPAT-77; historical PAT-75 depends-onPAT-74 | Claude14b4f180 graph / Codex explicit Epic creation; integrated 4559570 after PAT92 |
| linear-claude / D | PAT-78 PR3 head631bdbd10c21f8a0004b15546af1ed0c22ef41b2 / merge8772d63f26b58d866643103179a08b65c9d69540 | 82bf3e5e1475a85d57cab23d16f5c1c9f4726a20 / Claude source Sonnet historical |
| linear-claude / C | PAT-84 linear:epic:cbf12439d7e5ca84868dcf38f9a1aea5bb3fc40cae12aa6dc907a6a3286564d7 | human accepted operator closure; PAT92 merge5edae5165ffdc979407f79b2f4a1090000499f6d; integrated 4559570 |
| linear-claude / R | PAT61 sandbox v1: PAT-77/78/80/81 accepted; shared Apple tag v1.0 SHAac36ccac6916b657932bf29949a0227ac43b742c | per-host original release bridge; native Claude complete read14b4f180 and integrated 4559570 |
| linear-codex / B | github.com/patobiskoto/foundry-v1-pat61-linear-sandbox; project {'id': 'd3d412b6-1327-4bba-905b-01e4bb29797f', 'key': 'PAT'}; configuration sha256:e45ffccbcd795f39512acae828c2f8c84fff0b73d2faf418b5f4fddf7d8ee6c7 | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| linear-codex / A | PAT-ADR-0001 V0=0424d248-6f2d-46c1-92ed-96aa9694b188; edit9468058c-d53e-4255-9464-cd6eebb67588; link67a77aae-d2b8-4133-8acd-e4fd78395a65 | historical Codex native create/edit/link witness documented in recipe |
| linear-codex / G | PAT-83 -> PAT-77; PAT-78 depends-onPAT-77; historical PAT-75 depends-onPAT-74 | Claude14b4f180 graph / Codex explicit Epic creation; integrated 4559570 after PAT92 |
| linear-codex / D | PAT-74 PR1 merge855dd887f66c58a793c5bb782abacd33429383a4; PAT-77 PR2 headb087948ac65f90b0ad30ac48bf0a795907f4d0cb mergeecc85f449f9160730bc3b089edb2843dc287f159 | historical Codex nominal deliveries documented in recipe |
| linear-codex / C | PAT-83 linear:epic:68d62ab643e087354e76596c92627e5c890c9edcec9a7d61fb4d1184e0faeca5 | human accepted operator closure; PAT92 merge5edae5165ffdc979407f79b2f4a1090000499f6d; integrated 4559570 |
| linear-codex / R | PAT61 sandbox v1: PAT-77/78/80/81 accepted; shared Apple tag v1.0 SHAac36ccac6916b657932bf29949a0227ac43b742c | per-host original release bridge; native Claude complete read14b4f180 and integrated 4559570 |
| ghprojects-claude / B | github.com/patobiskoto/foundry-v1-pat61-ghprojects-recovery-sandbox; project {'id': 'PVT_kwHOABroCc4BlYLD', 'key': 'P61R'}; configuration sha256:52a4894c90867a0c2e7f7b149684715df8236aefa4863ba99211de1430b3a188 | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| ghprojects-claude / A | P61R-ADR-0008 / ref8 / proposed V1→V2 | dacb80232ef9bc1c09d5718bae3528e885ea63b4 / Claude 2.1.267 native |
| ghprojects-claude / G | P61R-9 -> P61R-6; P61R-5 depends-onP61R-6 (not required closed child) | dacb802 Claude graph/recovery; reread 4559570 |
| ghprojects-claude / D | P61R-6 PR7 corrected head afb8f906b43baf55e0b2fc8fc59ad443ec68749e mergea523f170c5ee553299f02a0a40a22799f77801ab | 8a4bbaecb5834f13f69fc9c5a954784020569344 / Claude 2.1.267 native Eiffel/Maigret |
| ghprojects-claude / C | P61R-9 github:epic:e2a46fb61b2ea34ecdb1cf829fe8406455a80fa7a2586f71d8b9a7d93a7aca39 | actual operator human accepted closure; native integrated 4559570 |
| ghprojects-claude / R | PAT61 Claude receipt qualification milestone2 / only P61R-6 accepted | dacb802 native common bridge; reread 4559570 |
| ghprojects-codex / B | github.com/patobiskoto/foundry-v1-pat61-ghprojects-recovery-sandbox; project {'id': 'PVT_kwHOABroCc4BlYLD', 'key': 'P61R'}; configuration sha256:52a4894c90867a0c2e7f7b149684715df8236aefa4863ba99211de1430b3a188 | historical synthetic bootstrap/host selection; final integration 4559570 (Claude native read, Codex existing binding retained) |
| ghprojects-codex / A | P61R-ADR-0003 ref 3; P61R-5 item PVTI_lAHOABroCc4BlYLDzg-PUMA | f743f2e / f9f420f |
| ghprojects-codex / G | P61R-4 -> P61R-1; P61R-5 depends-on P61R-6; P61R-6 blocks P61R-5 | f567764 / 8a4bbaec |
| ghprojects-codex / D | P61R-1 PR #2 head 67c5db9caec373d50afec7e2052b206a4f3b0c42; merge 43d31bfd5b32649b556ce24fdaa1ac3de8a362e3 | 662df7 |
| ghprojects-codex / C | P61R-4 audit github:epic:9ba37bf81a564a36cc437dde2d78409bb6e43aec900b5af48a5306d319f6f088 | f567764 and post-cleanup read |
| ghprojects-codex / R | PAT61 sandbox v1 milestone #1/id18273978; P61R-1 only | f567764 |

## Analyse d’impact et limites

- **B / A** : les bindings ne changent pas et sont indépendamment relus dans la même session Claude, un helper par cwd. L’évolution ADR est prouvée sur ses références d’origine et son corps terminal, avec l’attribution historique Linear et le nouvel edit natif V2→V3 détaillés ci-dessus. Aucun support ADR expérimental n’est promu. PAT-79 corrige l’acceptation synthétique étrangère, PAT-85 corrige les refus de transport : les incidents et huit BLOCK restent historiques, pas effacés.
- **G / C** : les liens ont été préparés avant les cinq clôtures réellement acceptées par le mainteneur. La session finale authentifie l’audit et compare exactement enfants/dépendances au snapshot courant. PAT-92 merge `5edae5165ffdc979407f79b2f4a1090000499f6d` corrige la projection Linear ; ses readbacks et groom sont sauvegardés. Parents AC0/2 et acceptation parent inconnue ne sont pas maquillés en receipts code. YouTrack reste `native-only`, Linear/GitHub `aligned`. La limite de prédécesseur pending/reopen PAT-92 reste documentée.
- **D** : les nominales propres à chaque hôte sont conservées : Claude P61Y-6/PAT-78/P61R-6, Codex P61Y-1/2 puis reçu neuf4, PAT-74/77 et P61R-1. Le replay final est une lecture d’intégration, pas un nouveau start/PR/merge ni une preuve fraîche des enfants Eiffel/Maigret. PAT-16 a qualifié les profils natifs et leur promotion sur ses refs ; ses observations ne sont pas déplacées. PAT-15 conserve les profils Codex ; 24 contrôles partagés couvrent l’impact code commun, sans prétendre à une nouvelle exécution fournisseur Codex.
- **R** : les scopes exacts acceptés sont YouTrack4/6, Linear77/78/80/81 et GitHub6 ; les bridges par hôte d’origine et les trois relectures natives finales sont nommés. YouTrack historique2/3 demeure `unavailable`, sans backfill. `native_capability=unavailable` pour fermeture release YouTrack/Linear correspond au contrat opérateur `record-scope-freeze` avec relecture de scope ; aucun état natif fermé n’est inventé. GitHub expose la fermeture du milestone par opérateur.
- **R / Apple partagé** : app 6818259217, tag v1.0 au SHA `ac36ccac6916b657932bf29949a0227ac43b742c`, Release #2 `92286db6-7b83-45ca-96f8-ba45c814d8e7` COMPLETE/SUCCEEDED, build `a3426daa-dfb8-4cbf-b3b6-7efd21b1ea64` VALID/1.0(2), archive et distribution interne SUCCEEDED. Le mainteneur a validé « c’est bon » pour installation iPhone, Hello World, fermeture complète et relance. Cette preuve réelle commune ne remplace aucun bridge tracker et ne prétend pas à six publications publiques. Les préparations simulateur et build de découverte sans distribution restent nommés dans l’histoire.
- **P61R** : Project#9 reste gelé/non résolu ; seul Project#10 est qualifié. P61R-5 dépend de6 mais n’est pas enfant requis clos et reste Backlog. La restauration après dérive du graphe P61R-4 conserve son audit original ; aucun unlink Foundry inventé, aucun parent forcé.
- **Distribution / économie** : installation propre ou upgrade officiel reste PAT-62 après publication. Phase intégrée achevée/épuisée : un parent, trois helpers séquentiels, quatre tours observés, 0 enfant / retry / crédit API ; plafond 12 tours / 3 USD, 0.0502504 USD catalogue observé, pas facture. Quota restant non observé. Les budgets antérieurs (dont PAT-16 sept parents/six enfants et quatre appels Codex) ne sont pas renouvelés. Aucun benchmark complet ni économie mesurée n’est revendiqué.

## Autres critères et sortie

| AC | Preuve / qualification |
| --- | --- |
| 1 : six parcours | 36 entrées ci-dessus et `journey_evidence`, avec création historique et évolution native V2→V3 Linear × Claude ; couverture proposée après correction du BLOCK, soumise à nouvelle revue. |
| 2 : conformité | Log `/tmp/pat61-final-integrated-conformance.log` :108 passed/3333 désélectionnés à 4559570 ; 24 shared-routing passed. Public 3418 / 10 exclus à f67fd35, code identique ; la validation locale finale ci-dessous couvre le contenu documentaire proposé avant revue. Aucun de ces tests n’est une preuve provider ou CI PAT-61. |
| 3 : adversaires | Les neuf observations du tableau de la recette sont conservées avec leurs limites : changement de cwd même session, collisions coordonnées, ADR étrangère, global contradictoire, tokens isolés absents, ADR liée à item archivé/restauré exactement, dérive marqueur, ancien tracker archivé, rollback octets. Ce sont des opérations réelles bornées et des préflights locaux, pas des tentatives de mutations dangereuses effectivement exécutées. |
| 4 : prérequis / migration | `/tmp/pat61-final-prerequisite-readback.json` :17 prérequis terminaux/AC complètes/accepted. Revalidation PAT-20/42/43/44/45/46/47/48 plus68 et merges exacts dans `/tmp/pat61-final-revalidation-report.json`. Les désaccords natifs historiques restent explicitement conservés. Cutover PAT-64 manifeste phase activated, copie ADR/source issues verified, source YouTrack P64Q archivée/cible GitHub P64G sélectionnée ; aucun état inconnu des relations/acceptation converti en vide. |
| 5 : Apple | Intégration tracker propre aux six parcours, effets internes bornés à l’app de test et validation iPhone déjà reçue ; chemin commun mutualisé. |
| 6 : bilan sans capacité inconnue promue | PASS proposé après complément natif AC1 ; BLOCK précédent conservé, sans verdict frais inventé. Effort runtime/quota inconnus, source 0.9.0 et limites ci-dessus préservés ; aucun receipt créé. |
| 7 : PAT-15/PAT-16 | Livraisons acceptées et promotions humaines relues ; PAT-16 PR70 mergec8c4073… et preuve ddecc855… dans le manifeste/handoff. Informations modèle/effort séparées, inconnu préservé. |
| 8 : replay / impact | Observation source 4559570 nativeClaude 2.1.285, trois helpers séquentiels couvrant les trois trackers dans une seule session ; analyse par étape ci-dessus. Contrôles Codex partagés sans nouvelle campagne. |
| 9 : code versus installation | Source 0.9.0 ; aucun cache édité ou prétendu1.0. PAT-62 garde publication puis install/upgrade officiel. |

**Évaluation proposée : les 36 étapes disposent désormais de preuves identifiées, dont l’évolution ADR native Linear × Claude V2→V3.** Le BLOCK AC1/AC6 antérieur reste historique ; seul un nouveau verdict indépendant peut valider cette correction. Les autres étapes gardent leurs producteurs historiques et les trois lectures intégrées ne deviennent pas un nouveau cycle mutable sous les défauts PAT-16. Restent les gates de livraison PAT-61 : revue indépendante, validations/tests et CI exacts, puis merge Foundry.

## Validation locale finale avant commit

Sur le contenu proposé (parent `4559570`, sans changement de code runtime) : recette3passed ; suite publique Foundry3421passed/10deselected ; conformité108passed/3334deselected ; contrat routing11passed ; Ship-iOS35passed/11subtests ; catalogue19tests et validateur dual-runtime verts ; Ruff et `git diff --check` verts. Logs locaux `/tmp/pat61-final-{public,conformance,routing,shipios,catalogue,ruff}.log`. Ces tests ne créent aucune preuve fournisseur. La review et la CI porteront sur le commit final de la PR, pas sur une réattribution des observations historiques.

## Correction AC1/AC6 et historique de revue

La revue précédente reste BLOCK : AC1 `not_covered`, AC6 `contradicted`, AC2–5/7–9
`pass`, qualité `blocked`. Sa preuve terminale
`4349771b741ceabb46506a2b59c84470b6fa562221a58547a3094d49af6fb5ca` est liée au
diff `608e5bc3f9092d5412574187bd42fd6471c5f4d32f6034b2c4976819901f31f7`,
génération 1 ; les neuf outcomes/digests sont conservés dans le manifeste. Le snapshot
AC a pour digest `f9a2ffabc545ab54942de27ebebde7395ef06659e161e8010432882e1edcbc30`.

Le complément source `4f142e5ce4baacbdbff7d1486c4870126b1afc91`, Claude 2.1.285,
Foundry 0.9.0, est lié à l’observation exacte par SHA256
`6694e463f5e83907f19156d7d0808e292dad02d1573f4be208521ddddabd0b26`.
Le corps ADR relu évolue de SHA256
`348166568146ab9a6664724b695e21ea4dcb6a1d231970f3a1e357b63ded481a` vers
`1e7bf0a658406145d732fde1ce3f5f7cd6b8018b1ed951893f26bdf1f3bb4e55`.
Le stream brut et les readbacks/helper/runner/autorisation sont pointés avec leurs
digests dans l’observation. Demandé Sonnet5.5/medium, transmis
`claude-sonnet-5-5`/medium, modèle runtime observé `claude-sonnet-5-5` ; effort runtime
non exposé et quota non observé restent inconnus. Succès en 2 tours, 0.0277176 USD
catalogue, pas facture. Budget distinct autorisé : 1 parent / 12 tours / 3 USD catalogue /
0 enfant / 0 reprise / 0 crédit API additionnel achevé et épuisé ; aucune invocation
supplémentaire ni renouvellement historique. Cette observation ajoute uniquement
l’évolution ADR Linear × Claude, sans effet sur les autres trackers, Epics, Apple
ou parcours PR historiques.

Validation locale du complément documentaire sur parent `4f142e5` :
`pytest -q tests/test_pat61_real_qualification_recipe.py` — 4 passed ; Ruff du test
et `git diff --check` verts. La copie native est byte-identique au fichier source ;
les six `partial_evidence`, les digests historiques et l’ancienne observation sont
identiques à HEAD. Ces contrôles ne remplacent pas la nouvelle revue ni la CI.

Validation locale du complément avant commit (parent4f142e5, runtime inchangé) :
4tests de recette,3422tests publics/10exclus,108conformité/3335désélectionnés,
11routing, Ruff et diffcheck verts. Logs `/tmp/pat61-v3-{public,conformance,routing,ruff}.log`.
La CI et la revue doivent porter sur le commit corrigé exact ; cette validation
locale ne remplace pas ces gates ni les preuves natives.
