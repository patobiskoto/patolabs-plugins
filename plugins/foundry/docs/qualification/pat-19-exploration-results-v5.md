# PAT-19 — Résultats de la comparaison d'exploration, version 5

PAT-127. Cadre : PAT-ADR-0015 (exploration jugée seulement par son effet aval, règle écrite d'avance et jamais changée après
avoir vu les résultats, une donnée absente n'est jamais zéro, preuve insuffisante = conserver le cloud, aucune promotion),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance), FOUNDRY-ADR-0010 (enveloppe), FOUNDRY-ADR-0015 (coût lu dans les
journaux de session), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Protocole gelé :
[`pat-19-protocol-v5.md`](pat-19-protocol-v5.md) (règles héritées de la [v4](pat-19-protocol-v4.md), de la
[v3](pat-19-protocol-v3.md) et de la [v2](pat-19-protocol-v2.md)) ; configuration : `pat-19-campaign-v5.json` ; vérité terrain :
`pat-19-exploration-truth-v2.json` ; lanceur : [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (section « Protocole v5 ») ;
boucle opérateur : [`pat-19-v5-operator.md`](pat-19-v5-operator.md) ; pièces des pilotes :
[`pat-19-v5-pilots-evidence.md`](pat-19-v5-pilots-evidence.md) ; modèle de ce document :
[`pat-19-exploration-results-v4.md`](pat-19-exploration-results-v4.md). Bilan de l'ensemble v1 à v5 :
[`pat-19-local-first-bilan.md`](pat-19-local-first-bilan.md).
Aucune règle ni coordonnée du protocole v5 n'est modifiée par ce document ; il consigne un essai réel, corrige une phrase
du protocole dans un erratum (section « Erratum ») parce qu'un protocole gelé ne s'édite pas, et s'arrête là.

Légende des marques. **[fichiers]** : recalculé sur les fichiers versés. **[flux]** : lu sur les flux bruts, qui restent hors
dépôt et ne peuvent donc pas être revérifiés depuis le dépôt. **[code]** : lu ou exécuté sur le code du dépôt (fonctions pures,
sans modèle ni cloud). **[coord.]** : observation du coordinateur, hors dépôt, non vérifiable d'ici. **[hypothèse]** : lecture
non établie. **[inconnu]** : non su. Un chiffre sans marque est un décompte [fichiers].

## Résumé

Une campagne, `pat-19-x5compare-1`, le 2026-10-08 (heure locale UTC+2, de 19:04 à 21:49) : comparaison A (Sonnet seul) contre L
(Sonnet avec le rapport de l'explorateur local qwen3.6-35b-a3b-mlx-4bit) sur les **12 tâches** du corpus, 12 lancements, 12
chargements du modèle (un avant chaque tâche), **74 exécutions cloud** (plafond 150), **19 308 267 tokens de facturation
premium** (A 11 433 953 ; L 7 874 314), 12 explorations locales (zéro token premium).

**Verdict tel que la règle gelée le rend** : décision `keep_cloud`, `campaign_conclusion` `keep_cloud`,
`paired_rule.reason` `not_retained_on_paired_set`, `failed_criteria` = [`economy`] ; compatibilité `pass`. L'ensemble apparié
D compte **10 tâches** (seuil 9 atteint). Sur D, **A et L acceptent chacun 5 tâches** (acceptation tenue : 5 ≥ 5) ; la prime
par tâche acceptée est de **1 835 739** tokens pour A et **1 574 863** pour L, soit un **rapport de 0,8579 pour un seuil de
0,85** : le critère d'économie **échoue, de peu** (L est 14 485 tokens par tâche acceptée au-dessus de la ligne, soit 0,93 %).
Le rapport imprime `recommendation: A`. Par PAT-ADR-0015, le cloud est conservé. Aucune promotion, aucun modèle ni profil
local activé, aucun gain de facture annoncé.

Ce que ce verdict ne dit pas : il ne dit pas que L est moins cher, ni presque aussi bon ; il dit que le critère pré-enregistré
n'est pas rempli. Les chiffres qui montrent à quel point la mesure est mince et bruitée sont donnés plus bas, **hors règle et
sans valeur de décision** : 10 tâches, 5 acceptées par bras, des ensembles de tâches acceptées qui diffèrent sur 4 des 10,
aucun essai répété, des douze tâches déjà vues.

Deux explorations locales (PR 42 et PR 33) ont été comptées contaminées et rendent ces deux tâches indécidées dans le bras L
(elles quittent D). Dans les deux cas, le modèle local a **écrit le chemin d'un fichier qui n'existe pas** ; le flux montre qu'aucun
fichier n'a été lu à ce chemin (voir « Les deux explorations contaminées »). Cela n'a coûté aucun token et a retiré 2 tâches
sur 12 de la comparaison.

## Conduite de l'essai

- **Code** : outillage gelé `main` à `d173e17` (PAT-126 fusionné), extrait hors dépôt ; Claude Code 2.1.285 (journal opérateur) ;
  harnais omp et LM Studio non revérifiés pour cette campagne [inconnu]. Contexte demandé 65 536 (`-c 65536`), **observé 262 144 à
  chacun des 12 chargements** (journal opérateur) : le plancher est respecté, la variable n'est toujours pas contrôlée.
  Empreintes (enregistrements) : campagne `fbac0921…`, manifeste `8ac65091…` (le même que les v2 à v4), enveloppe `902df72a…`.
- **Enveloppe** `pat-19-x5compare-1` : mode `compare_exploration`, **150 exécutions cloud, 75 000 000 tokens premium, 100 000 s**
  (valeurs recommandées par le protocole, section 4). Plafonds non atteints (74 exécutions, 19 308 267 tokens) : le plafond
  n'est pas exercé par cet essai, on ne sait pas ce qu'il aurait fait.
- **Mandat** [coord.] : le 2026-10-08, le mainteneur a écrit « Ok v5 et on va jusqu'au run et bilan. La machine est à toi toute la
  journée, tu peux lancer et arrêter ce qui doit l'être », puis « Ok fait comme tu dis » (candidat local unique qwen3.6-35b) et
  « Ok c'est bon » (règle de décision v5). Le chargement du modèle pour la campagne est couvert par ce mandat **de bloc** : un
  rechargement avant chaque tâche, **non confirmé un par un** ; la boucle opérateur charge avec `-y`. Ces accords sont dans la
  conversation, pas dans le dépôt ; l'accord explicite du mainteneur sur la règle v5 a été consigné en commentaire sur PAT-126 avant la
  fusion [coord.] (le texte gelé du protocole et `rules_applied` du rapport disent encore « à consigner » : ils précèdent l'accord).
- **Sauvegarde** [coord.] : l'état Foundry du mainteneur (`~/.config/foundry`) a été copié hors dépôt (dossier horodaté 19:03:49)
  avant le lancement ; non versée (elle contient des secrets), non lue par moi. `$FOUNDRY_DATA` : le script de lancement ne l'exportait
  pas ; l'environnement du shell parent n'a pas été relevé [inconnu].
- **Machine dédiée** : l'application ChatGPT et OrbStack ont été arrêtés pour la campagne (OrbStack au-delà de 2 Gio fait refuser le
  préflight de machine dédiée, déjà le cas du premier lancement de la v4) [coord.] ; OrbStack a été relancé ensuite, ses 9
  conteneurs sont revenus, avec les mêmes noms que la liste relevée avant le pilote (comparaison du coordinateur) [coord.]. Dans le registre
  versé [fichiers] : **24 préflights, 24 acceptés** (12 « de départ », 12 par tâche), aucun refus ; plus gros autre processus
  relevé 1 049 Mio (Claude), sous le seuil de 2 Gio ; mémoire libre minimale 58 % (seuil 35 %) ; l'interpréteur du lanceur et
  les deux interpréteurs des bras importent pytest (version 9.1.1 relevée).
- **Boucle** [fichiers] : journal opérateur, **12 lancements** (`launch 1` à `launch 12`), chacun précédé de `lms unload --all` et d'un
  chargement épinglé, les onze premiers finissant par `work_remains=yes`, le douzième par `work_remains=no` ; **code de sortie 0
  à chaque lancement** ; de 17:03:54Z à 19:49:05Z (2 h 45 min). Aucun arrêt par le plafond (code 3), aucun arrêt par le garde du
  registre (code 4) : le journal n'a aucune sortie non nulle et aucun enregistrement ne porte `foundry_state_changed`.
- **Registre réel** [coord.] : même préfixe de sha256 avant et après (`83bc256ad42fd3ae`, fichier `registry-before.txt` relu par moi
  pour l'« avant » ; l'« après » est celui du coordinateur) ; je n'ai pas lu `~/.config/foundry`. Le garde automatique du lanceur
  n'a rien relevé [fichiers] (`stops` vide).
- **Bornes de l'explorateur local** : 900 s et 60 étapes, un lancement par tâche, ordre des tâches de la configuration, bras A
  avant l'exploration de L pour une même tâche (ordre non contrebalancé, limite héritée). Le candidat, le retour au correcteur
  (20 noms de tests, 300 caractères par message, 2 corrections), la racine privée, le bac à sable natif et le mode `dontAsk` sont
  ceux du protocole gelé ; **rien n'a été changé après avoir vu un résultat**.
- **Fraîcheur du rapport** : rapport produit par l'outillage gelé (`d173e17`) avec le verbe `report` (lecture seule) ; je n'ai pas
  relancé `report` ; tous les chiffres ci-dessous sont recalculés sur les fichiers versés et coïncident avec lui (voir
  « Concordance avec le rapport »).

## Comparaison (`pat-19-x5compare-1`)

### Issues par bras et par tâche

Recalculées sur `results-pat-19-x5compare-1.jsonl` (**69 enregistrements** `attempt` : 12 explorations locales du bras L, 30
tentatives cloud du bras A, 27 du bras L). « Tentatives » = tentatives cloud (rounds), l'exploration locale de L étant comptée à part (« +1 »).
« Refusé » = refusé par le juge (tests protégés cachés). « Indécidé » = enregistrement de la tâche contaminé (règle gelée).

| PR | D | A : tokens, tentatives, issue finale | L : tokens, tentatives (+ exploration), issue finale |
| --- | --- | --- | --- |
| 26 | oui | 431 922, 2, **accepté** | 603 584, 2 (+1), **accepté** |
| 38 | oui | 474 054, 3, refusé par le juge | 567 243, 3 (+1), **accepté** |
| 25 | oui | 1 110 200, 3, **accepté** | 1 299 103, 3 (+1), `review_block` |
| 42 | non | 1 718 609, 3, refusé par le juge | 0, 0 (+1), exploration **contaminée** (indécidé) |
| 33 | non | 536 649, 1, **accepté** | 0, 0 (+1), exploration **contaminée** (indécidé) |
| 37 | oui | 1 284 269, 3, **accepté** | 590 681, 2 (+1), **accepté** |
| 30 | oui | 454 193, 3, refusé par le juge | 346 048, 3 (+1), refusé par le juge (exploration refusée : bornée à 900 s) |
| 83 | oui | 2 015 388, 1, **accepté** | 1 150 994, 3 (+1), refusé par le juge |
| 27 | oui | 651 552, 2, **accepté** | 689 273, 2 (+1), **accepté** |
| 24 | oui | 618 968, 3, refusé par le juge | 1 301 414, 3 (+1), **accepté** |
| 48 | oui | 1 268 359, 3, refusé par le juge | 576 569, 3 (+1), refusé par le juge |
| 19 | oui | 869 790, 3, refusé par le juge | 749 405, 3 (+1), refusé par le juge |
| Total 12 | | 11 433 953 ; 30 tentatives ; **6 acceptées** | 7 874 314 ; 27 tentatives ; **5 acceptées** |

Tâches acceptées sur les 12 : A 6 (26, 25, 33, 37, 83, 27), L 5 (26, 38, 37, 27, 24). Issues finales de L : 5 acceptées, 1
`review_block` (PR 25), 4 refusées par le juge (PR 83, 30, 48, 19), 2 explorations contaminées (PR 42, 33). Issues finales de A : 6
acceptées, 6 refusées par le juge (PR 38, 42, 30, 24, 48, 19). Aucune issue `review_unreadable`, `review_contaminated`,
`tool_error`, `stopped_by_cap` ni `interrupted` : les tâches indécidées de la campagne sont exactement PR 42 et PR 33 du bras L.

**Ensemble apparié D** = 10 tâches décidées dans les deux bras : 19, 24, 25, 26, 27, 30, 37, 38, 48, 83. Tâches acceptées sur D :
A 5 (25, 26, 27, 37, 83), L 5 (24, 26, 27, 37, 38). **Les ensembles de tâches acceptées diffèrent sur 4 des 10 tâches** : A accepte
PR 25 et PR 83 que L n'accepte pas ; L accepte PR 38 et PR 24 que A n'accepte pas. Sur D, L est moins cher que A en tokens sur 5
tâches (37, 30, 83, 48, 19) et plus cher sur 5 (26, 38, 25, 27, 24).

### Table par tentative (cloud)

Complète, issue des résultats versés. « Retour reçu » = compteurs `feedback` de l'enregistrement (tests cachés échoués montrés au
correcteur / total échoués) ; vide pour le tour 0 et pour le tour qui suit un BLOCK de la revue (le retour de revue est inchangé).
Aucun échec de collecte enregistré (`collection_failure` faux partout). « Tokens premium » = `billing_total` de l'enregistrement
(implémenteur ou correcteur, plus le relecteur).

| PR | Bras | Tour | Juge (passés / échoués) | Retour reçu (montrés / total) | Revue | Issue enregistrée | Tokens premium |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 26 | A | 0 | REFUSED (0 / 1) | — | — | `judge_refused` | 115 611 |
| 26 | A | 1 | ACCEPTED (1 / 0) | 1 / 1 | PASS | `accepted` | 316 311 |
| 26 | L | 0 | REFUSED (0 / 1) | — | — | `judge_refused` | 184 137 |
| 26 | L | 1 | ACCEPTED (1 / 0) | 1 / 1 | PASS | `accepted` | 419 447 |
| 38 | A | 0 | REFUSED (28 / 8) | — | — | `judge_refused` | 150 862 |
| 38 | A | 1 | REFUSED (34 / 2) | 8 / 8 | — | `judge_refused` | 187 445 |
| 38 | A | 2 | REFUSED (33 / 3) | 2 / 2 | — | `judge_refused` | 135 747 |
| 38 | L | 0 | REFUSED (28 / 8) | — | — | `judge_refused` | 126 090 |
| 38 | L | 1 | REFUSED (35 / 1) | 8 / 8 | — | `judge_refused` | 130 450 |
| 38 | L | 2 | ACCEPTED (36 / 0) | 1 / 1 | PASS | `accepted` | 310 703 |
| 25 | A | 0 | REFUSED (3 / 2) | — | — | `judge_refused` | 169 111 |
| 25 | A | 1 | ACCEPTED (5 / 0) | 2 / 2 | BLOCK | `review_block` | 242 745 |
| 25 | A | 2 | ACCEPTED (5 / 0) | — | PASS | `accepted` | 698 344 |
| 25 | L | 0 | REFUSED (4 / 1) | — | — | `judge_refused` | 239 455 |
| 25 | L | 1 | ACCEPTED (5 / 0) | 1 / 1 | BLOCK | `review_block` | 532 055 |
| 25 | L | 2 | ACCEPTED (5 / 0) | — | BLOCK | `review_block` | 527 593 |
| 42 | A | 0 | REFUSED (0 / 4) | — | — | `judge_refused` | 816 378 |
| 42 | A | 1 | ACCEPTED (4 / 0) | 4 / 4 | BLOCK | `review_block` | 733 802 |
| 42 | A | 2 | REFUSED (2 / 2) | — | — | `judge_refused` | 168 429 |
| 33 | A | 0 | ACCEPTED (172 / 0) | — | PASS | `accepted` | 536 649 |
| 37 | A | 0 | REFUSED (1 / 1) | — | — | `judge_refused` | 259 838 |
| 37 | A | 1 | ACCEPTED (2 / 0) | 1 / 1 | BLOCK | `review_block` | 514 689 |
| 37 | A | 2 | ACCEPTED (2 / 0) | — | PASS | `accepted` | 509 742 |
| 37 | L | 0 | REFUSED (1 / 1) | — | — | `judge_refused` | 225 958 |
| 37 | L | 1 | ACCEPTED (2 / 0) | 1 / 1 | PASS | `accepted` | 364 723 |
| 30 | A | 0 | REFUSED (161 / 2) | — | — | `judge_refused` | 125 231 |
| 30 | A | 1 | REFUSED (161 / 2) | 2 / 2 | — | `judge_refused` | 163 331 |
| 30 | A | 2 | REFUSED (161 / 2) | 2 / 2 | — | `judge_refused` | 165 631 |
| 30 | L | 0 | REFUSED (161 / 2) | — | — | `judge_refused` | 145 602 |
| 30 | L | 1 | REFUSED (161 / 2) | 2 / 2 | — | `judge_refused` | 79 182 |
| 30 | L | 2 | REFUSED (161 / 2) | 2 / 2 | — | `judge_refused` | 121 264 |
| 83 | A | 0 | ACCEPTED (18 / 0) | — | PASS | `accepted` | 2 015 388 |
| 83 | L | 0 | REFUSED (16 / 2) | — | — | `judge_refused` | 533 579 |
| 83 | L | 1 | REFUSED (16 / 2) | 2 / 2 | — | `judge_refused` | 377 224 |
| 83 | L | 2 | REFUSED (16 / 2) | 2 / 2 | — | `judge_refused` | 240 191 |
| 27 | A | 0 | REFUSED (2 / 1) | — | — | `judge_refused` | 212 942 |
| 27 | A | 1 | ACCEPTED (3 / 0) | 1 / 1 | PASS | `accepted` | 438 610 |
| 27 | L | 0 | REFUSED (2 / 1) | — | — | `judge_refused` | 206 233 |
| 27 | L | 1 | ACCEPTED (3 / 0) | 1 / 1 | PASS | `accepted` | 483 040 |
| 24 | A | 0 | REFUSED (1 / 9) | — | — | `judge_refused` | 390 526 |
| 24 | A | 1 | REFUSED (9 / 1) | 9 / 9 | — | `judge_refused` | 131 527 |
| 24 | A | 2 | REFUSED (4 / 6) | 1 / 1 | — | `judge_refused` | 96 915 |
| 24 | L | 0 | REFUSED (1 / 9) | — | — | `judge_refused` | 205 711 |
| 24 | L | 1 | ACCEPTED (10 / 0) | 9 / 9 | BLOCK | `review_block` | 625 017 |
| 24 | L | 2 | ACCEPTED (10 / 0) | — | PASS | `accepted` | 470 686 |
| 48 | A | 0 | REFUSED (223 / 122) | — | — | `judge_refused` | 587 850 |
| 48 | A | 1 | REFUSED (223 / 122) | 20 / 122 | — | `judge_refused` | 323 448 |
| 48 | A | 2 | REFUSED (223 / 122) | 20 / 122 | — | `judge_refused` | 357 061 |
| 48 | L | 0 | REFUSED (223 / 122) | — | — | `judge_refused` | 158 165 |
| 48 | L | 1 | REFUSED (223 / 122) | 20 / 122 | — | `judge_refused` | 274 490 |
| 48 | L | 2 | REFUSED (223 / 122) | 20 / 122 | — | `judge_refused` | 143 914 |
| 19 | A | 0 | REFUSED (5 / 4) | — | — | `judge_refused` | 132 255 |
| 19 | A | 1 | REFUSED (7 / 2) | 4 / 4 | — | `judge_refused` | 567 176 |
| 19 | A | 2 | REFUSED (7 / 2) | 2 / 2 | — | `judge_refused` | 170 359 |
| 19 | L | 0 | REFUSED (5 / 4) | — | — | `judge_refused` | 175 538 |
| 19 | L | 1 | REFUSED (5 / 4) | 4 / 4 | — | `judge_refused` | 141 416 |
| 19 | L | 2 | REFUSED (5 / 4) | 4 / 4 | — | `judge_refused` | 432 451 |

Un « ACCEPTED » du juge avec un BLOCK du relecteur est enregistré `review_block` (la revue décide) ; les six BLOCK de la
campagne sont à la section « Blocages de la revue ». Sur PR 25 (A, tour 1) et PR 37 (A, tour 1), un BLOCK a été suivi d'un tour
accepté ; sur PR 42 (A, tour 1), un BLOCK a été suivi d'un tour qui régresse (4 / 0 puis 2 / 2) et la tâche finit refusée.

### Verdict, tel que la règle gelée le rend

Lecture [fichiers], dans l'ordre de la règle (`paired_rule` du rapport, recoupé sur les enregistrements) :

1. |D| = 10 ≥ 9 : le verdict n'est pas `inconclusive` pour D trop petit. Aucune prime inconnue sur D ; aucun départ sans
   enregistrement, aucune exécution cloud non soldée (`ledger.unknown_spent_work` vide, `unsettled_starts` vide), aucun rejeu,
   aucune tâche vide (`void_attempts` vide), aucun arrêt (`stops` vide) : aucune raison de campagne (`campaign_level_reasons` vide).
2. **Acceptation** : acceptés_L(D) = 5, acceptés_A(D) = 5 ; 5 ≥ 5, **tenue** (au sens de la règle : L n'accepte pas moins que A).
3. Cas zéro : acceptés_A(D) = 5 ≥ 1, ne s'applique pas.
4. **Économie** : premium sur D : A **9 178 695**, L **7 874 314** tokens (les trois bras comptés : implémenteur, correcteur,
   relecteur ; tous modèles ; jetons locaux exclus). Par tâche acceptée : A 9 178 695 / 5 = **1 835 739** ; L 7 874 314 / 5 =
   **1 574 863** (arrondi ; 1 574 862,8). Rapport 1 574 862,8 / 1 835 739 = **0,8579** (arrondi à 4 décimales). Seuil : 0,85 × 1 835
   739 = 1 560 378. **L dépasse la ligne de 14 485 tokens par tâche acceptée** : le critère d'économie **échoue**, de peu.
5. Compatibilité : `pass` (aucune exploration terminée par un signal externe ; supplément de swap par exploration au plus 0 MiB,
   mesuré sur les 12 ; le `signal` 9 de PR 30 est l'arrêt par le lanceur à la borne de 900 s). Lue en dernier.
6. Donc : acceptation tenue, économie en échec → « conserver le cloud » : décision **`keep_cloud`**, `paired_rule.reason`
   `not_retained_on_paired_set`, `failed_criteria` `["economy"]`, `campaign_conclusion` `keep_cloud`, `recommendation` `A`.
   Le rapport imprime aussi `arms.L.quality` = `pass` et `arms.L.economy` = `fail` : ce sont les lectures sur D (protocole v5, section 2,
   point 7 ; posées par `_apply_paired_rule`) ; les lectures des v2 à v4 ne sont pas imprimées. Seul `paired_rule.ratio` décide.

**Rien n'est recalculé avec un autre seuil ni un autre ensemble.** Le verdict est celui de la règle gelée avant la campagne, y
compris le seuil de 9 tâches de D, choisi par jugement sans dérivation statistique (le rapport le dit).

### Concordance avec le rapport

Recalculé, identique au rapport : 10 tâches de D ; acceptés A 5, L 5 sur D ; prime sur D A 9 178 695, L 7 874 314 ; par tâche
acceptée A 1 835 739,0, L 1 574 862,8 ; rapport 0,8579 ; totaux sur les 12 tâches A 11 433 953, L 7 874 314 ; acceptés sur 12
A 6, L 5 ; 74 exécutions cloud ; temps de L 5 546,764 s dont 2 507,628 s d'exploration locale ; temps de A 4 112,018 s.
**Écarts avec la note de cadrage de PAT-127** : aucun chiffre ne diverge. Une affirmation de la note est contredite par les
fichiers : voir l'erratum (cas (ii)).

## Ce que chaque bras a fait

- **Bras A** (30 tentatives, 39 exécutions cloud) : 6 tâches acceptées sur 12 ; deux d'entre elles dès le tour 0 (PR 33, 537 k ;
  PR 83, 2 015 k tokens, la tâche la plus chère du bras, 22 % de la prime de A sur D). Quatre des 6 acceptations ont demandé au
  moins un tour de correction (PR 26, 25, 37, 27). Six tâches finissent refusées par le juge, dont PR 30 et PR 48 (comptes
  constants à chaque tour : 161 / 2 et 223 / 122) et PR 42 (un tour accepté par le juge puis BLOCK, puis régression).
- **Bras L** (27 tentatives cloud, 35 exécutions, + 12 explorations locales) : 5 tâches acceptées sur 12, **toutes après au moins
  un tour de correction** (aucune acceptation au tour 0). Une tâche finit en `review_block` (PR 25 : juge accepté aux tours 1 et 2,
  BLOCK aux deux). PR 30, 48 et 19 : comptes constants ou presque. PR 83 : trois tentatives à 16 / 2 identiques, alors que A l'a
  acceptée du premier coup.
- **Coût par rôle** (quatre classes de jetons de facturation, sans les jetons de raisonnement séparés ; hors règle) : sur D, A :
  implémenteur 3 457 293, correcteur 3 209 177, relecteur (Opus) 2 512 225 ; L : implémenteur 2 200 468, correcteur 2 972 315,
  relecteur (Opus) 2 701 531. La différence de prime sur D (1 304 381 en faveur de L) se décompose en 1 256 825 sur
  l'implémenteur, 236 862 sur les correcteurs, et −189 306 sur les relecteurs (L en a davantage). Le relecteur Opus pèse
  27 % de la prime de A sur D et 34 % de celle de L. Les lectures de cache pèsent 88 % (A) et 86 % (L) des tokens de
  facturation sur 12 tâches ; les tokens de sortie 2 % ; la somme non pondérée traite un token de cache lu comme un token généré.
  Ces décompositions ne disent pas ce qui causerait la différence : les deux bras n'ont pas accepté les mêmes tâches.

## Ce que le retour de tests a changé

Fait recalculé sur les tours de correction (30 tours suivent un refus du juge : 15 pour A, 15 pour L ; tous portent des compteurs
`feedback`, 30 enregistrements, aucun échec de collecte) :

| Après un refus du juge | Tours | Comptes de tests changés | Tour suivant sans test échoué (0 échoué) |
| --- | --- | --- | --- |
| A | 15 | 10 | 5 |
| L | 15 | 7 | 6 |
| Total | 30 | **17** | **11** |

Un tour suivant un BLOCK de revue (sans retour de tests) : 5 (A 3, L 2) ; un seul change les comptes, en régression (PR 42 A,
4 / 0 puis 2 / 2). Les 11 tours qui arrivent à 0 échoué sont, pour A, PR 26, 25, 42, 37 et 27, et pour L, PR 26 (0 / 1 puis 1 / 0), 25, 38 (35 / 1
puis 36 / 0), 37 (1 / 1 puis 2 / 0), 27 (2 / 1 puis 3 / 0) et 24 (1 / 9 puis 10 / 0) ; un tel tour n'est pas toujours accepté
(PR 25 L, PR 24 L tour 1 et PR 42 A finissent en BLOCK ou régressent). Les tâches dont les comptes ne bougent jamais malgré le retour : PR 30 (161 / 2, 4 tours
sur 4), PR 48 (223 / 122, 4 sur 4, 20 noms montrés sur 122), PR 83 L (16 / 2, 2 sur 2), PR 19 L (5 / 4, 2 sur 2). PR 30 et PR 48
présentent **les mêmes comptes que dans la v4** (161 / 2 ; 223 / 122) : aucun bras de l'une ni de l'autre campagne n'a bougé ces
deux tâches ; on ne sait pas pourquoi [inconnu].

Lecture [hypothèse, non établie] : le retour de tests semble aider les deux bras à finir des tâches (toutes les acceptations de
L, 4 sur 6 de A viennent après au moins un tour de correction), mais le protocole (v4 §3.1 et §5) dit déjà que le message peut
contenir l'expression d'une assertion et les valeurs attendues ; un compte qui passe de 1 / 9 à 10 / 0 ne distingue pas une meilleure
correction d'un ajustement au message. Il n'y a pas de bras sans retour dans la v5.

## Les deux explorations contaminées

Le rapport liste 2 enregistrements `contaminated` (`commands` vide pour les deux), tous deux **exploration locale du bras L** :
PR 42 et PR 33. Aucun enregistrement cloud n'est contaminé. Les deux sont `exploration.report` `null` : **par la règle** (`verdict_of` :
une exploration contaminée perd son rapport, elle est notée `CONTAMINATED`) [code], et non parce que le message final était vide :
les messages finaux des deux flux contiennent un bloc JSON [flux], non lu pour être noté (la règle gelée interdit de le faire
compter). Leur tâche est indécidée dans L et quitte D (le rapport : `undecided.L` = {33, 42}, cause `explore:contaminated`, premium 0).

**Ce que les flux montrent** [flux] :

- **PR 42** : le modèle local a tapé six fois des chemins où le nom de la racine privée porte un `/` à la place du `-`
  (`private-attempt-l04-0005/pr42-L-explore-…` au lieu de `private-attempt-l04-0005-pr42-L-explore-…`) : cinq appels `grep` sur le
  dossier `plugins/foundry/docs` et un appel `read` sur `registry.py`. Les six résultats sont des erreurs d'outil : le `grep` répond
  `Path not found: <chemin>`, le `read` répond `Path '<chemin>' not found`. Le modèle a ensuite utilisé les chemins corrects
  (31 appels d'outil en tout, 6 en erreur).
- **PR 33** : un seul appel en erreur, un `read` de `frame.py` à un chemin déformé
  (`private-attempt-l05-0003-pr33-L-explore-qwen3.6-33-L-explore-qwen3.6-35b-a3b-mlx-4bit/bundle/…`, un nom qui répète un fragment) ;
  résultat `Path '<chemin>' not found`. 35 appels d'outil en tout.
- Dans les deux cas, **aucun fichier n'a été lu à ce chemin** : l'outil a dit que le fichier n'existe pas. Le même type de faute
  est présent sur PR 26 L (un `grep` sur `linear.py` avec le `/`, résultat `Path not found: …`), qui n'est **pas** contaminée.
  C'est la même famille de faute que la PR 48 L de la v4 (diagnostic de la v4, cas 6).

**Pourquoi l'un est contaminé et pas l'autre** [code, hypothèse sur la cause] : l'audit reconnaît un chemin absent seulement quand le
résultat de l'outil commence par la phrase exacte `Path not found: ` (constante `_NOT_FOUND`, `local_first_runner.py`) ; il le range alors dans
`audit.not_found` (cas PR 26 et les cinq `grep` de PR 42, enregistrés ainsi). L'outil `read` répond par une autre phrase
(`Path '…' not found`) : l'appel n'est pas reconnu comme « absent » et son chemin, sous la racine de travail (sensible), est relevé.
C'est l'explication la plus simple des deux contaminations et de la non-contamination de PR 26 ; je ne l'ai pas validée par un
rejeu de l'audit (qui demanderait d'exécuter le lanceur), c'est donc une hypothèse appuyée sur la lecture du code et des flux.
Corriger l'audit est du travail futur (intake), non traité ici.

**Coût** : zéro token premium (L joue 0 pour ces deux tâches), 332 s d'exploration locale (135 s et 197 s) ; effet sur la
comparaison : 2 tâches sur 12 retirées de D (D = 10 au lieu de 12). On ne sait pas ce que le verdict aurait été avec 12 tâches
[inconnu]. D reste au-dessus du seuil de 9 : la perte n'a pas rendu le verdict `inconclusive`.

## Journal de l'audit (ne décide rien)

Sous la politique de la v5 (le bac à sable est la barrière, l'audit un journal) : `audit.barrier` vaut
`settings_transmitted_version_observed` pour les **57 enregistrements cloud**, `not_verified` pour les **12 enregistrements
d'exploration locale** (aucun bac à sable cloud ne s'applique à un explorateur local ; ce n'est pas une barrière manquante).
Veille du dossier temporaire (`audit.temp_leftovers.watch`) : `complete` pour les 57 enregistrements cloud, `not_applicable` pour les 12 locaux ;
aucune entrée déplacée (recomptage [fichiers]). **Appels refusés par l'hôte : 49** (Bash 48, Write 1 ; A 28, L 21 ; 33
enregistrements concernés).

Le rapport compte **13 enregistrements avec journal** ; en recomptant, **21 entrées** (le rapport attribue 13 entrées au bras et 9 au
relecteur, soit 22 attributions : une entrée est attribuée aux deux, le PR 38 L tour 2) :

| Nature de l'entrée | Entrées | Enregistrements |
| --- | --- | --- |
| `tool_result:~/.config/foundry/config.env` (écho d'un échec de test) | 10 | 10 : A PR 38 (tours 1 et 2), PR 42 (tour 1), PR 37 (tour 2), PR 27 (tour 1) ; L PR 26 (tour 1), PR 38 (tours 0, 1 et 2), PR 24 (tour 2) |
| `/<unknown-working-directory>` (un `cd` que l'audit ne sait pas placer) | 3 | L PR 26 (tour 1), A PR 37 (tour 2), L PR 37 (tour 1) : le rapport les attribue au relecteur pour les deux L ; A PR 37 (tour 2) compte 3 entrées pour 3 attributions (bras 1, relecteur 2) : aucune n'y est double (attribution non relue) |
| chemin sous la racine de travail, commande de l'arme | 7 | L PR 30 (tour 0, six chemins) ; A PR 83 (tour 0, un chemin) |
| `tool_result:` d'un dossier privé d'une autre tentative | 1 | A PR 37 (tour 2) |

- **Écho de `config.env`** [flux] : dans 12 flux cloud (11 avec la trace pytest, 1 en prose), on lit `PermissionError: [Errno 1]
  Operation not permitted` sur le fichier de configuration Foundry du mainteneur (home masqué ici) : **un test du dépôt
  tente de lire cette configuration** et le bac à sable natif le refuse. Le fichier n'est lu par aucune commande de l'arme (aucune
  commande ne le nomme) ; c'est un écho de sortie de test. Répartition : 5 enregistrements A, 5 L. Les relecteurs de trois
  BLOCK (PR 25 L tour 1, PR 37 A tour 1, PR 24 L tour 1) citent aussi « 156 échecs » de la suite complète, qu'ils attribuent au
  bac à sable (chiffre rapporté par eux, non recompté). **Effet sur l'issue d'un bras : inconnu** [inconnu]. C'est un effet
  d'instrument observé de la v5, déjà vu au pilote 4 : un test du dépôt qui touche la configuration du home échoue sous le bac à sable.
- **PR 30 L, tour 0** [flux] : les six commandes Bash de cet appel sont des chaînes `cd` (`cd plugins 2>/dev/null; cd ..; grep -rIl …
  .`, `cd plugins/foundry; …`, `cd docs; …`, `cd ../../..; … ls docs plugins/foundry/docs`, `cd plugins/foundry; ls docs/adr …`) ; par
  l'arithmétique des `cd`, avec un dossier de travail conservé d'un appel à l'autre, **elles restent dans le bundle** [hypothèse
  de lecture : je n'ai pas rejoué l'audit]. Le journal les range sous la racine de travail : l'audit les résout contre la racine de
  travail au lieu du dossier courant, c'est le sur-signalement connu des chaînes `cd` ordinaires (limite L de PAT-123) [hypothèse].
- **PR 83 A, tour 0** [flux] : la commande `… grep -n … ../../../tests/test_linear_tracker.py`, lancée après une série de `cd`
  dans `tooling/foundry/trackers`, désigne le fichier de test du bundle (`plugins/foundry/tests`) si le dossier courant est bien
  celui-là [hypothèse de lecture] ; le journal la range sous la racine de travail, comme si le chemin était résolu contre la racine
  du bundle. C'est le même geste, sur la même tâche, le même bras et le même tour que le cas 3 de la v4
  (qui avait alors été marqué `contaminated`). Ici le journal ne décide rien et le tour est accepté.
- Aucun `review_unreadable`, aucun `review_contaminated` ; les entrées côté relecteur n'ont écarté aucun verdict.

## Blocages de la revue

17 verdicts de relecteur : **11 PASS, 6 BLOCK** (A : 6 PASS, 3 BLOCK ; L : 5 PASS, 3 BLOCK). Aucun verdict illisible. Les
enregistrements ne portent que le verdict ; les raisons sont lues dans le dernier message de chaque session de relecteur
[flux], résumées sans les généraliser :

| Tentative | Raison de blocage (relecteur) |
| --- | --- |
| A PR 25, tour 1 | aucun test ajouté alors que la tâche exige des tests sur faux transport ; vérification de collision avec un id existant propre à Linear (« majeur ») ; documentation incomplète |
| L PR 25, tour 1 | régression dans le préflight (`list_adrs` appelée pour tout frame Linear, une réparation après interruption échoue avant `create_adr`), établie par lecture, non reproduite (script d'essai refusé) ; la réciprocité n'est pas prouvée par les tests |
| L PR 25, tour 2 | la garde de forme d'identifiant (`[A-Za-z]+-ADR-\d+`) laisse passer des clés de projet avec chiffres, **reproduite sur faux transport** ; les tests ne prouvent pas la branche « id existant » |
| A PR 42, tour 1 | pointeur de reprise sans issue (« un nouveau cutover » impossible tant qu'un marqueur existe), établi par lecture ; clé ou id qui dérive sans indication de reprise ; procédure qui ne restaure qu'un arbre de travail |
| A PR 37, tour 1 | pas de test sur faux transport (la tâche le demande) ; documentation des skills non mise à jour (AGENTS.md R5) |
| L PR 24, tour 1 | documentation non mise à jour (`linear-tracker.md`, `frame/SKILL.md`, AGENTS.md R5), le correctif lui-même est jugé correct |

Lecture : trois BLOCK sur six (A PR 25, A PR 37, L PR 24) portent sur des tests ou de la documentation manquants ; trois (L PR 25
tours 1 et 2, A PR 42) sur un défaut de logique ou de procédure, dont un seul reproduit. Cinq relecteurs sur six écrivent qu'un
script d'essai a été refusé par le mode de permission et que leurs constats reposent sur la lecture du code. Je ne conclus
rien de plus : six verdicts d'un seul modèle de relecteur, sur des tâches différentes. Le BLOCK n'est pas propre à un bras (3 et 3).
Contrairement à la v4, aucun des six flux de relecteur ne contient `pytest_cache` ni `.ruff_cache` (recherche de texte
sur les flux entiers) [flux] : le défaut de capture du correctif relevé en v4 et corrigé par PAT-123 ne se retrouve pas dans ces six BLOCK.

## Qualité de l'exploration sur les 12 tâches

Notée par le même juge de localisation ; **informatif**, la règle de décision ne lit pas ces valeurs. Recalculé depuis
`exploration.score` et `local` de chaque enregistrement d'exploration (le rapport imprime les mêmes valeurs).

| PR | Verdict | Rappel fonctions | Précision fichiers | Rappel fichiers | Secondes (étapes) |
| --- | --- | --- | --- | --- | --- |
| 26 | scoré | 1,0 | 1,0 | 1,0 | 158 (28) |
| 38 | scoré | 1,0 | 1,0 | 1,0 | 70 (15) |
| 25 | scoré | 1,0 | 1,0 | 1,0 | 108 (25) |
| 42 | CONTAMINÉ | 0,0 | 0,0 | 0,0 | 135 (31) |
| 33 | CONTAMINÉ | 0,0 | 0,0 | 0,0 | 197 (35) |
| 37 | scoré | 0,5 | 0,667 | 1,0 | 101 (23) |
| 30 | REFUSÉ (coupée par la borne de temps, 900 s, 60 étapes) | 0,0 | 0,0 | 0,0 | 900 (60) |
| 83 | scoré | 1,0 | 0,5 | 1,0 | 217 (31) |
| 27 | scoré | 1,0 | 1,0 | 1,0 | 159 (19) |
| 24 | scoré | 0,0 | 1,0 | 1,0 | 128 (30) |
| 48 | scoré | 1,0 | 1,0 | 1,0 | 178 (20) |
| 19 | scoré | 0,5 | 1,0 | 1,0 | 155 (32) |

Sur les 9 explorations scorées : rappel de fichiers 1,0 pour les 9 ; précision moyenne de fichiers 0,907 ; rappel moyen de
fonctions **0,778** (6 sur 9 à 1,0, deux à 0,5, une à 0,0 : PR 24, même valeur 0,0 qu'au tamis v3 et à la v4). Moyenne sur les 12
avec refus et contamination à 0 (hypothèse héritée du filtre) : rappel de fonctions 0,583, précision de fichiers 0,681, rappel
de fichiers 0,75. Ces moyennes sont des calculs séparés, non la règle. Le candidat et le budget ont été retenus sur le score de
six de ces tâches (protocole §5) : ces chiffres ne mesurent pas une généralisation. Les explorations de PR 42 et PR 33 produisent
un JSON dans leur message final qui n'est pas noté, de sorte qu'on ne sait pas ce qu'elles auraient valu [inconnu].

Compatibilité (mécanique) : `pass` ; supplément de swap maximal 0 MiB (le swap a baissé de 8 MiB sur PR 42), `ended_by_external_signal` faux sur
les 12 (PR 30 : `timed_out` vrai, `signal` 9, l'arrêt par le lanceur à la borne de temps). Jetons du flux local : 11 433 053 en entrée,
104 272 en sortie, 49 121 de raisonnement, 349 étapes (zéro token premium) ; temps d'exploration 2 507,6 s au total ; débits non
exposés par le flux [inconnu]. Le temps et la mémoire du côté local ne sont **pas chiffrés** dans la prime.

## Coût

Définition (rapport) : somme non pondérée des quatre classes de tokens de facturation, tous modèles, relecteur (Opus) compris ;
les lectures de cache comptent comme n'importe quel token ; les jetons locaux ne comptent pas.

| Bras | Exécutions cloud | Tokens premium (12 tâches) | Tokens premium sur D |
| --- | --- | --- | --- |
| A | 39 | 11 433 953 | 9 178 695 |
| L | 35 | 7 874 314 | 7 874 314 |
| Total | **74** | **19 308 267** | |

Plafond de 150 exécutions et de 75 000 000 tokens : non atteint. Par modèle (12 tâches) : A, Sonnet 8 050 520 et Opus 3 383 433 ; L,
Sonnet 5 172 783 et Opus 2 701 531. Temps cloud : A 4 112 s, L 3 039 s (plus 2 508 s d'exploration locale).

Économie sur D : voir « Verdict ». **Aucun gain de facture n'est annoncé.** Le total de L sur les 12 tâches est plus petit que celui de A
(7 874 314 contre 11 433 953), mais L n'a rien dépensé sur les deux tâches indécidées et n'a pas accepté les mêmes tâches : ce
total ne se compare pas.

## Lectures hors règle, non décisionnelles : à quel point la mesure est mince

Rien ci-dessous ne remplace ni ne recalcule le verdict ; ce sont des calculs sur les fichiers versés pour mesurer la fragilité
de la mesure, **labellisés comme tels**.

- **Marge** : 0,8579 contre 0,85, soit 0,93 % de la ligne. Avec 5 tâches acceptées par bras, un seul changement d'issue d'une tâche
  déplace beaucoup plus que cette marge.
- **Sensibilité à une tâche** (rapport recalculé sur D privé d'une tâche, comptes d'acceptation recalculés) : en retirant PR 25,
  0,652 ; PR 83, 0,751 ; PR 26, 0,831 ; PR 27, 0,843 (sous 0,85) ; PR 19, 0,858 ; PR 30, 0,863 ; PR 37 et PR 48, 0,923 ; PR 24, 0,960 ;
  PR 38, 1,049. Six des dix retraits laissent le rapport au-dessus de 0,85, quatre en dessous ; sans PR 24 ou sans PR 38, L accepterait 4 tâches
  contre 5 pour A (l'acceptation échouerait). L'étiquette dépend de la tâche que l'on retire. Ce n'est pas une estimation de variance
  statistique, et ce n'est pas une règle de décision.
- **Stochasticité par tentative** : PR 83 est acceptée par A du premier coup (2,0 M tokens) alors que L perd quatre tentatives
  (une exploration et trois rounds à 16 / 2) ; PR 24 : A trois rounds refusés, L accepté. Rien ne dit que ces écarts ne se
  renverseraient pas à la répétition.
- **Aucun essai répété** : un seul passage par couple tâche / bras ; la variabilité d'un passage à l'autre n'est pas mesurée.
- **Relecteur dans la prime** : le relecteur Opus compte pour 27 % (A) et 34 % (L) de la prime sur D ; sur D, L compte 8 verdicts
  de relecteur contre 7 pour A.
- **Côté local non chiffré** : les 2 508 s d'exploration locale, la mémoire et l'énergie n'entrent pas dans la prime.
- **Tâches non indépendantes** : les 12 tâches ont déjà été jouées dans les campagnes antérieures, et toutes ont été vues par l'explorateur local ;
  neuf modifient `tests/test_linear_tracker.py` (protocole §5).

## Critères d'arrêt (a) à (f) des pilotes

Le critère d'arrêt (a) à (f) de la section 7.2 du protocole est celui des pilotes. Lu sur le rapport et les enregistrements,
**aucun arrêt n'a été déclenché pendant la campagne** : pas de tâche vide, pas de relecteur illisible, `audit.barrier` = `settings_transmitted_version_observed` pour
chaque enregistrement cloud (réglages transmis et version observée ; le lanceur n'observe pas l'application du bac à sable par le système, protocole §3.1), registre réel inchangé [coord.], exploration locale exécutée (12 lancées, 1 coupée par
la borne). C'est une lecture du rapport, pas un nouveau verdict. Une question reste ouverte pour le coordinateur : les deux
explorations contaminées, où le drapeau tient à un chemin inexistant tapé par le modèle, ressemblent au critère (a) (drapeau
écartant un enregistrement sans que le bras ait quitté son essai) ; le critère était écrit pour les pilotes et je ne rends pas de verdict dessus.

## Erratum du protocole v5, section 2, cas (ii)

Le protocole v5 est gelé ; la correction est donc portée ici. La section 2 écrit, pour le cas (ii) (« `inconclusive` en v2 à v4,
`keep_cloud` en v5 »), que « les v2 à v4 n'y voyaient aucun échec : qualité `unavailable` entre ses bornes, économie `unavailable` ».

- **Économie** : exact. `economy_verdict` rend `unavailable` dès qu'une tâche comparée est indécidée (`cut`) [code].
- **Qualité/acceptation** : par définition, le cas (ii) suppose des v2 à v4 `inconclusive` ; l'écart réel est que l'énumération des
  situations couvre aussi des campagnes `keep_cloud` des deux côtés (qualité `fail` déjà en v2 à v4) et que la parenthèse omet la
  qualité `pass`. `quality_verdict` lit l'acceptation **par bornes** : `pass` si L tient dans
  le pire cas (acceptés_L minimum ≥ acceptés_A maximum), `fail` si L échoue même dans le meilleur cas (acceptés_L maximum <
  acceptés_A minimum), `unavailable` entre les deux [code]. Une tâche indécidée peut donc déjà donner un **échec** de la qualité
  dans les v2 à v4 ; l'étiquette est alors `keep_cloud` des deux côtés et le cas n'est **pas** un cas (ii). La parenthèse ne vaut que pour les
  campagnes où la qualité est `unavailable` ou `pass` (jamais `fail`) en v2 à v4.
- **Cette campagne** : contrairement à ce que la note de cadrage de PAT-127 attendait, **elle est dans le cas (ii)**. Recalculé avec
  les fonctions du dépôt sur les enregistrements versés [code] : en lecture v2 à v4, la qualité de L est `unavailable` (acceptés_L :
  minimum 5, maximum 7 avec les deux tâches indécidées ; acceptés_A : 6 ; 5 < 6 et 7 ≥ 6), l'économie est `unavailable` (deux tâches
  indécidées), la compatibilité `pass` : décision **`inconclusive`**. La v5 rend `keep_cloud`, parce que les deux tâches indécidées quittent D
  et que l'économie y est lue et échoue. Les deux étiquettes recommandent le cloud (A) ; la conclusion de campagne serait
  `keep_cloud_insufficient_evidence` en v2 à v4, `keep_cloud` en v5. Le verdict `keep_cloud` plutôt qu'`inconclusive` est donc l'effet
  du traitement des tâches indécidées de la v5, fixé avant la campagne ; il n'est pas modifié ici.

## Ce qui reste inconnu

- Ce que l'économie aurait été avec 12 tâches dans D (explorations de PR 42 et PR 33 contaminées) ; ce que le juge aurait rendu sur L
  pour ces deux tâches.
- L'effet des 49 appels refusés par l'hôte et de l'écho de `config.env` (10 enregistrements) sur les issues de chaque bras.
- Si l'écart de 0,0079 en rapport (0,8579 contre 0,85) survivrait à une répétition ; la variance d'un passage à l'autre.
- Pourquoi les comptes de PR 30 (161 / 2) et PR 48 (223 / 122) ne bougent dans aucun bras de la v4 ni de la v5.
- La part du contexte observé (262 144 au lieu de 65 536) dans la variabilité de l'exploration.
- La cause exacte de la contamination des deux explorations (hypothèse de la phrase `Path … not found` appuyée sur le code, non rejouée).
- Les versions exactes d'omp et de LM Studio pour cette campagne : non relevées dans les journaux versés.
- Hors dépôt, non vérifiables d'ici : la sauvegarde, le sha256 du registre réel après, l'état d'OrbStack et de ChatGPT, les accords
  donnés dans la conversation, le fait que l'application de bureau livre sa propre copie 2.1.293 de Claude Code [coord.].

## Écarts et limites

- **Chargements de modèles** couverts par le mandat de bloc du mainteneur, non confirmés un par un ; **OrbStack et ChatGPT** arrêtés
  le temps de la campagne ; aucun lancement refusé cette fois (24 préflights acceptés).
- Les limites du protocole v5 (section 5) valent telles quelles : **les 12 tâches ont déjà été jouées** (non indépendantes : neuf
  modifient `tests/test_linear_tracker.py`, quatre PR d'une même branche empilée) et vues par l'explorateur local ; **la PR 27 a servi à
  régler l'instrument** (pilotes, essais du bac à sable) ; le rapport de L leur est probablement favorable ; **pas de bras Haiku**
  (Haiku 5.5 demande Claude Code 2.1.293 ou plus, la machine a la 2.1.285 [fichiers : journal opérateur] ; l'application de bureau livre sa propre
  copie 2.1.293 [coord.], fait à consigner pour PAT-125, **sans décision**) ; **dossier temporaire partagé** par utilisateur (AGENTS.md R6
  interdit de changer `TMPDIR`) ; **le mode `dontAsk` rend la v5 non comparable à la v4** ; une interruption après règlement à la manche
  0 donnerait un verdict inconclusif (limite dite du protocole, non exercée ici).
- À ces limites s'ajoutent celles de ce résultat : **deux explorations perdues pour un chemin mal tapé**, un seul passage par couple
  tâche / bras, **un test du dépôt qui touche la configuration du home et échoue sous le bac à sable** (effet d'instrument, 10 enregistrements), un
  seul modèle de relecteur, un seul candidat local, une seule machine, un seul moteur local (MLX).
- Douze tâches ne sont **pas** une preuve générale ; ce résultat ne dit rien d'autres familles de tâches ni d'un explorateur cloud
  économique ni d'un autre rôle local.

## Pièces versionnées

Dans [`pat-19-runs/x5compare-1/`](pat-19-runs/x5compare-1/), copiées telles quelles : `ledger-pat-19-x5compare-1.jsonl`,
`results-pat-19-x5compare-1.jsonl`, `report-pat-19-x5compare-1.json`, `envelope.json`, `operator-compare-qwen3.6-35b-a3b-mlx-4bit-20261008T170354Z.log`,
`streams-manifest.json` (sha256 et taille des 86 flux : 74 sessions cloud et 12 explorations locales, environ 45 Mo). `driver.log`
n'est pas versé (comme pour la v4). Les flux bruts ne sont pas versionnés (ils embarquent des contenus du dépôt et de la machine) ;
ils restent sur la machine du mainteneur. Le manifeste ne donne que des noms de session et d'exploration (identifiants de session,
noms `attempt-lNN-NNNN-prNN-L-explore-…`), jamais un chemin du dossier personnel.

**Contrôle avant versement** : recherche du préfixe des dossiers personnels, du nom d'utilisateur, d'adresses, de jetons et de clés dans le registre, les
résultats, le rapport, l'enveloppe, le journal opérateur et le manifeste : **aucune occurrence**. Aucun fichier n'a été masqué ni modifié :
chaque pièce est identique à sa source (le masquage du home en `~` est déjà fait par le lanceur dans les enregistrements : le texte
`~/.config/foundry/config.env` y figure tel quel). Les chemins de la racine de travail (`/private/tmp/…`) et les chemins d'interpréteurs système
restent, comme dans `x4compare-1` et `x5pilot-*`. Le journal opérateur garde les séquences d'affichage de progression du chargement.
Le dossier `state/streams/`, `driver.log`, `report.err` (vide), `registry-before.txt` et la sauvegarde restent hors dépôt.

## Statut documentaire (R5)

Artefacts ajoutés : ce document, le bilan [`pat-19-local-first-bilan.md`](pat-19-local-first-bilan.md) et
`pat-19-runs/x5compare-1/` ; un lien dans la section « Protocole v5 » de `pat-19-launcher-v1.md` ; une entrée de CHANGELOG. Aucun
verbe ni option de `foundry_cli.py`, clé de configuration, constante publique ou table de routage n'a changé ; protocole v5,
configuration v5 et résultats v1 à v4 inchangés ; aucun code. Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé
ici et vérifié en revue, non appliqué mécaniquement.
