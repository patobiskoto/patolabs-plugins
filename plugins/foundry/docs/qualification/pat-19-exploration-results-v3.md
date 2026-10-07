# PAT-19 — Résultats du tamis et de la comparaison d'exploration, version 3

PAT-117. Cadre : PAT-ADR-0015 (exploration jugée seulement par son effet aval, tamis local sans cloud à règle écrite
d'avance, trois verdicts séparés, aucune promotion, une donnée absente n'est jamais zéro hors de ce filtre),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance). Protocole gelé :
[`pat-19-protocol-v3.md`](pat-19-protocol-v3.md) (règles héritées de [`pat-19-protocol-v2.md`](pat-19-protocol-v2.md)) ;
configuration : `pat-19-campaign-v3.json` ; vérité terrain : `pat-19-exploration-truth-v2.json` ; lanceur :
[`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (section « Protocole v3 ») ; boucle opérateur :
`pat-19-v3-operator.sh` ; modèle de ce document : [`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md).
Aucune règle ni coordonnée du protocole v3 n'est modifiée par ce document ; il consigne deux essais réels et s'arrête là.
Les nombres ci-dessous sont des décomptes recalculés ; toute lecture est marquée « lecture » ou « hypothèse ».

## Résumé

**Incident à lire d'abord** : pendant la comparaison, un bras cloud a écrasé le registre Foundry du mainteneur en lançant les tests du dépôt (voir « Incident ») ; fichier restauré le même jour ; les ENREGISTREMENTS de la campagne sont intacts, mais l'influence de l'incident sur l'issue des tentatives jouées après 10:03:46 est INCONNUE (voir « Incident ») ; la conclusion mécanique est inchangée ; correctif : PAT-120 ; aucun bras cloud à relancer avant lui.

Deux campagnes, le 2026-10-07 (heure locale, UTC+2) :

- **Tamis `pat-19-x3screen-1`** (08:23 à 09:13, 12 lancements, 0 exécution cloud) : les deux candidats passent les seuils
  gelés (rappel moyen de fonctions >= 0,5, précision moyenne de fichiers >= 0,5). qwen3.6-35b-a3b-mlx-4bit : rappel de
  fonctions **0,639**, précision de fichiers 0,958, rappel de fichiers 1,0, 0 refus. qwen3-coder-30b-a3b-mlx-4bit : 0,583 /
  0,667 / 0,667, 2 refus. **Retenu : qwen3.6** (rappel moyen de fonctions le plus haut). Aucune contamination, aucun
  enregistrement nul, aucun rejeu.
- **Comparaison `pat-19-x3compare-1`** (09:15 à 10:38, 6 lancements, bras A, L, E sur les 6 tâches de comparaison) :
  **51 exécutions cloud** (plafond 120), **11 101 326 tokens de facturation premium** au total. Tâches acceptées : **A 0
  sur 6, L 0 sur 6, E 1 sur 6** (PR 25). Rapport mécanique : décision `inconclusive`, `campaign_conclusion`
  `keep_cloud_insufficient_evidence` (campagne complète ; qualité et économie `unavailable` pour L). Par PAT-ADR-0015, des
  preuves insuffisantes conservent le cloud. Aucune promotion, pas de rejeu sous la v3.

Ce que les données soutiennent : **le bras de référence A n'a fait accepter aucune tâche**, et la comparaison n'a donc pas pu
mesurer l'effet aval de l'exploration. Décomposition recalculée des 0 sur 6 de A : 3 tâches (PR 26, 38, 25) refusées par le
juge aux 3 tours, et 3 tâches (PR 42, 33, 37) **jamais jugées** (`judge: null`, drapeaux de contamination que ce document
tient pour des faux positifs probables, non arbitrés) : la moitié du zéro vient de l'audit, pas du corpus ni du juge. Autres
faits : sur PR 33 le juge a accepté L et E (A n'a pas été jugé) ; sur PR 25 l'instrument a discriminé (E accepté, A et L
refusés). Lecture (hypothèse) : le résultat du tamis v1 (0 sur 30 pour des implémenteurs locaux, sans référence cloud) doit
être lu à la lumière de ce que la comparaison n'a pas pu mesurer ; ce n'est pas un résultat pour ou contre le local.

Les 7 drapeaux de contamination ont tous la même forme (un bras cloud a nommé la racine de travail du lanceur). Ils sont
appliqués tels que gelés et **non arbitrés** (voir « Contamination »).

## Conditions de l'essai

- Code : `main` à `00eee40` (PAT-116 fusionné), arbre de travail détaché gelé ; harnais omp 18.6.1 ; LM Studio
  0.4.25+1 ; contexte demandé 65 536 (`-c 65536`, plancher gelé `min_context`). **Contexte observé** par le script
  opérateur (`lms ps`, journaux versés) : **262 144 pour qwen3.6** (les 12 chargements, tamis et comparaison, malgré `-c 65536`)
  et **65 536 pour qwen3-coder** (6 chargements) ; le plancher est respecté, mais les deux candidats classés n'ont pas tourné
  au même contexte. Empreinte de campagne `95b7a071…` (identique dans les deux rapports), manifeste `8ac65091…`.
  Enveloppes : `pat-19-x3screen-1` mode `screen_exploration`, 0 exécution cloud, 0 token premium, 36 000 s ;
  `pat-19-x3compare-1` mode `compare_exploration`, 120 exécutions cloud, 60 000 000 tokens premium, 100 000 s.
- Bornes de l'explorateur local : 900 s et 60 étapes (v3). Un lancement par tâche (`one_task_per_launch`) : le script
  opérateur décharge (`lms unload --all`) puis recharge le modèle avant chaque lancement ; les journaux opérateur versés
  montrent 6 lancements par candidat au tamis et 6 à la comparaison, chacun se terminant par `work_remains=yes` sauf le
  dernier (`work_remains=no`). Le lanceur ne charge ni ne décharge lui-même.
- Registre : tamis 12 préflights (12 acceptés), 12 tentatives ; comparaison 12 préflights (6 « de départ » et 6 par tâche,
  12 acceptés), 6 sessions. Aucun refus de préflight dans les deux campagnes.
- Machine dédiée (condition du protocole) : application ChatGPT fermée, services d'un autre projet arrêtés (observation du
  coordinateur, hors dépôt, absente du registre).
- Candidat de la comparaison : qwen3.6 (le préflight et l'explorateur L du registre le nomment). Le script opérateur
  `pat-19-v3-operator.sh` passe `--screening-campaign pat-19-x3screen-1` au lanceur (comportement documenté du script ; le
  journal opérateur versé ne consigne pas la ligne de commande) et les résultats et le registre du tamis ont été copiés en
  lecture seule dans le dossier d'état de la comparaison (observation du coordinateur ; ces copies ne sont pas versées).
  Pourtant le rapport versé de la comparaison affiche `exploration_screening.campaign_conclusion` `incomplete_screening` et
  `screening_selected` `screening_results_not_available` : **le lien entre le tamis et la comparaison n'est donc pas attesté
  mécaniquement par le rapport versé** ; il repose sur le script, le registre (qui nomme qwen3.6) et le rapport du tamis. La
  conclusion de la comparaison est celle de `exploration_comparison`.

## Tamis (`pat-19-x3screen-1`)

Recalculé à partir de `results-pat-19-x3screen-1.jsonl` (12 enregistrements `attempt`), rapproché de
`report-pat-19-x3screen-1.json` (mêmes valeurs). Un refus compte 0 sur toutes les métriques (règle du filtre).

| Candidat | Rappel fonctions | Précision fichiers | Rappel fichiers | Secondes | Refus | Pic de swap (MiB) |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3.6-35b-a3b-mlx-4bit | 0,639 | 0,958 | 1,0 | 1101,5 | 0 | 5803,0 |
| qwen3-coder-30b-a3b-mlx-4bit | 0,583 | 0,667 | 0,667 | 1847,3 | 2 | 11702,19 |

Par tâche (rappel de fonctions ; entre parenthèses précision de fichiers, secondes, étapes) :

| PR | qwen3.6 | qwen3-coder |
| --- | --- | --- |
| 30 | 1,0 (1,0 ; 232,2 s ; 33) | 1,0 (1,0 ; 302,1 s ; 56) |
| 83 | 0,333 (0,75 ; 270,1 s ; 48) | REFUSÉ : aucun rapport JSON dans le message final (285,9 s ; 43) |
| 27 | 1,0 (1,0 ; 131,9 s ; 22) | 1,0 (1,0 ; 167,8 s ; 30) |
| 24 | 0,0 (1,0 ; 115,6 s ; 25) | 1,0 (1,0 ; 73,7 s ; 34) |
| 48 | 1,0 (1,0 ; 246,7 s ; 29) | REFUSÉ : coupé par la borne de 900 s (900,0 s ; 47) |
| 19 | 0,5 (1,0 ; 105,0 s ; 23) | 0,5 (1,0 ; 117,7 s ; 47) |

Règle appliquée (gelée) : les deux candidats ont une précision moyenne >= 0,5 ; le meilleur rappel moyen de fonctions
(0,639) est >= 0,5 ; il n'y a pas d'égalité ; retenu = qwen3.6, raison `highest_mean_function_recall`. Tamis complet
(`complete` vrai, aucune tâche indécidée). Les deux candidats passent les seuils ; seul le rang les départage. Le rappel de
fichiers reste quasi trivial (une tâche = un fichier produit, cinq tâches sur six partageant `linear.py`) ; c'est le rappel
de fonctions qui décide.

Comparaison avec la v2 sur les mêmes six tâches (mêmes seuils) : qwen3.6 avait **0,25** de rappel de fonctions et 0,333 de
précision de fichiers (25 étapes, 10 minutes) ; qwen3-coder 0,167 / 0,167. Limite du protocole, énoncée plutôt que
contournée : **le budget, le jeu de candidats et le régime de chargement ont changé ensemble** (le rechargement avant chaque
tâche remplace un chargement par candidat) ; la différence v2 vers v3 n'est donc pas attribuable au seul budget. De plus,
le tamis v3 est ajusté sur ces six tâches (budget élargi après avoir vu leurs résultats v2) : son résultat est un filtre,
non une mesure de généralisation.

Supplément de swap par tentative (après moins avant) : maximum **+5963,2 MiB (5,82 Gio)**, qwen3-coder, PR 48 (la tentative
coupée à 900 s), sous le seuil de 10 Gio ; qwen3.6 : 0 ou négatif sur ses six tentatives. Le pic absolu de qwen3-coder
(11 702,19 MiB) reste au-dessus de 10 Gio mais le critère porte sur le supplément. Les causes (modèle, moteur, mémoire) ne
sont pas établies.

## Comparaison (`pat-19-x3compare-1`)

### Issues par bras et par tâche

Recalculées à partir de `results-pat-19-x3compare-1.jsonl` (53 enregistrements `attempt` : 6 explorations locales du bras L,
6 explorations Haiku du bras E, 41 tentatives d'implémentation cloud). « Refusé » = refusé par le juge (tests protégés
cachés). « Contaminé » = drapeau d'audit (section suivante).

| PR | A | L | E |
| --- | --- | --- | --- |
| 26 | refusé aux 3 tours | refusé aux 3 tours | refusé aux 3 tours |
| 38 | refusé aux 3 tours | refusé aux 3 tours | refusé aux 3 tours |
| 25 | refusé aux 3 tours | refusé aux tours 0 et 1, contaminé au tour 2 | tour 0 : juge ACCEPTÉ, revue BLOCK ; tour 1 : juge ACCEPTÉ, revue PASS : **accepté** |
| 42 | contaminé au tour 0 | refusé aux 3 tours | contaminé au tour 0 |
| 33 | contaminé au tour 0 | juge ACCEPTÉ, revue PASS, mais contaminé (tour 0) | juge ACCEPTÉ, revue BLOCK, contaminé (tour 0) |
| 37 | contaminé au tour 0 | refusé aux 3 tours | refusé aux 3 tours |

Tâches acceptées : **A 0 sur 6, L 0 sur 6, E 1 sur 6**. Tâches indécidées (contaminées) : A 3, L 2 (PR 25 et 33), E 2 (PR 42
et 33). Tentatives acceptées par le juge : A 0 ; L 1 (PR 33, revue PASS, non comptée car la tentative est contaminée) ;
E 3 (PR 25 deux fois, PR 33 une fois avec revue BLOCK, contaminée). Les tâches que le juge a refusées à tous les tours :
A 3, L 4, E 3.

### Exécutions cloud et tokens premium

Définition (rapport) : somme non pondérée des quatre classes de tokens de facturation, tous modèles, explorateur inclus ;
les lectures de cache comptent comme n'importe quel token (les explorations Haiku sont en grande partie des lectures de
cache : pour `claude-haiku-4-5-20251001` dans le bras E, 5 237 209 tokens de cache lu sur 5 499 632 de facturation (somme des
quatre classes ; les 21 502 tokens de raisonnement du rapport ne sont pas dans cette somme), soit environ 95 %).

| Bras | Exécutions cloud | Tokens premium | Détail |
| --- | --- | --- | --- |
| A | 12 | 1 556 805 | 12 tentatives d'implémentation |
| L | 17 | 1 895 995 | 16 tentatives d'implémentation (PR 33 : 2 exécutions) ; 0 pour les 6 explorations locales |
| E | 22 | 7 648 526 | 6 explorations Haiku (5 499 632 ; 567 615 à 1 539 306 chacune) + 16 exécutions d'implémentation (2 148 894) |
| Total | **51** | **11 101 326** | plafond 120 exécutions : non atteint |

Verdicts par le rapport pour L : économie `unavailable` (aucune tâche acceptée dans A ni dans L, donc pas de ratio par
tâche acceptée) ; qualité `unavailable` (tâches indécidées) ; compatibilité `pass`. Pour E (informatif) : une tâche acceptée,
7 648 526 tokens par tâche acceptée, pas de ratio (A n'en a aucune).

### Qualité de l'exploration sur les six tâches de comparaison

Jamais utilisées pour le réglage, notées par le même juge ; **informatif, pas une règle de décision**. Recalculé depuis
`exploration.score` de chaque enregistrement d'exploration.

| PR | L : rappel fonctions | L : précision fichiers | L : rappel fichiers | L : secondes (étapes) | E : rappel fonctions | E : précision fichiers | E : secondes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 26 | 1,0 | 1,0 | 1,0 | 100 (22) | 1,0 | 1,0 | 100 |
| 38 | 1,0 | 0,75 | 1,0 | 81 (23) | 1,0 | 1,0 | 58 |
| 25 | 1,0 | 1,0 | 1,0 | 117 (26) | 1,0 | 1,0 | 82 |
| 42 | 0,75 | 1,0 | 1,0 | 209 (49) | 0,75 | 0,5 | 93 |
| 33 | 1,0 | 1,0 | 1,0 | 243 (35) | 0,5 | 1,0 | 99 |
| 37 | 1,0 | 0,5 | 1,0 | 218 (30) | 0,5 | 1,0 | 78 |
| Moyenne | **0,958** | 0,875 | 1,0 | 81 à 243 | **0,792** | 0,917 | 58 à 100 |

L : zéro token premium. E : 567 615 à 1 539 306 tokens premium par exploration. Le rappel de fichiers de E est 1,0 sur les
six. Aucune exploration n'a été coupée par une borne ; aucun signal externe.

Lecture (hypothèse, non testée) : pour le rappel de fonctions, l'explorateur local égale ou dépasse l'explorateur Haiku sur
chacune des six tâches ; pour la précision de fichiers, non (0,875 contre 0,917 ; L est plus bas sur PR 38 et PR 37, plus
haut sur PR 42) ; cela ne dit rien de l'effet aval, que cette comparaison n'a pas pu mesurer. Six tâches ne font pas une preuve
générale.

### Compatibilité

`pass` par le rapport (critère machine : pas de signal externe, supplément de swap sous 10 Gio). Sur les six explorations
locales, le supplément de swap par tentative est au maximum 0 MiB (le swap a baissé de 87,4 MiB au plus) ; `ended_by_external_signal`
faux sur les six.

## Contamination

Le rapport liste **7** tentatives cloud contaminées : A PR 42, 33, 37 (tour 0) ; L PR 25 (tour 2), PR 33 (tour 0) ; E PR 42,
33 (tour 0). Les 7 ont la même forme : le bras cloud a nommé la **racine de travail du lanceur** (le parent du répertoire de
sa tentative) dans un chemin ou un glob. Valeurs enregistrées : `/private/tmp/pat19-work` (5 tentatives),
`/private/tmp/pat19-work/README.md` (A PR 42), `/private/tmp/pat19-work/*.md` (A PR 37), et pour L PR 33 les deux valeurs
`/private/tmp/pat19-work` et `/private/tmp/pat19-work/README.md`. **Aucune commande interdite** (`commands` vide pour les
7), aucun chemin du répertoire personnel.

Deux des sept drapeaux (L PR 33 et E PR 33) viennent du **relecteur**, non du bras d'implémentation (vérifiable dans les résultats versés : ces deux enregistrements, et eux seuls, portent sous `unknown.contaminated` le
texte « the reviewer's tool calls… », et ce sont les deux seules tentatives contaminées avec une revue, donc 2 exécutions cloud). La tentative de L que le juge a acceptée avec une
revue PASS est donc perdue sur un drapeau du relecteur.

Conséquence : 3 des 6 tâches de A, 2 de L et 2 de E sont indécidées ; une tentative de L que le juge a acceptée et que la
revue a passée n'est pas comptée.

État : **faux positif probable, NON arbitré.** Observation du coordinateur, hors dépôt, non vérifiable depuis le dépôt :
avec une tâche par lancement et les tentatives jetées, la racine de travail ne contenait que le répertoire de la tentative
en cours. L'audit signale un ancêtre du bundle par règle (conservé volontairement en PAT-112). **La règle a été appliquée
telle que gelée ; rien n'a été reclassé**, aucune tentative n'a été rejouée. Les lacunes d'audit documentées depuis
PAT-111 / PAT-113 pour le cloud seul (un appel refusé par une règle de refus compte comme contamination ; corps de
heredoc d'interpréteur non audités) n'ont pas été corrigées avant cette comparaison ; **aucune des 7 contaminations n'en
provient** (toutes sont de la forme racine de travail ci-dessus, `commands` vide).

## Incident : un bras cloud a écrasé le registre Foundry du mainteneur

Pendant la comparaison, le 2026-10-07 à 10:03:46 (heure locale), le registre Foundry du mainteneur (fichier `registry.json` de
son dossier de configuration Foundry, hors dépôt) a été remplacé par une fixture de test. Cause établie par le flux brut de la
session concernée (hors dépôt) : le bras L de la tâche PR 42 (implémenteur cloud, tour 0) a lancé `python3 -m pytest tests`
dans son bundle ; à cette base du dépôt, `tests/test_registry_sharing.py` posait la variable `FOUNDRY_DATA_DIR` au lieu de
`FOUNDRY_DATA` avant d'appeler `registry._save(...)`, donc la fixture a été écrite dans le vrai registre. Les bras cloud
tournent sans bac à sable, avec le vrai HOME (limite déclarée depuis PAT-111, risque résiduel accepté par le mainteneur le
2026-10-05) : ni les règles Bash interdites ni l'audit de contamination ne voient l'effet de bord d'un test exécuté, et cette
tentative n'a d'ailleurs pas été signalée contaminée.

Conséquences : toutes les commandes Foundry de la machine ont refusé (« binding tracker du marqueur absent du registre »)
jusqu'à la restauration ; le coordinateur n'a rien réécrit (le registre contient d'autres projets du mainteneur) ; le mainteneur
a restauré le fichier le même jour depuis un instantané Time Machine local antérieur à l'incident (fichier d'origine, daté du
2026-10-04). Parmi les fichiers du dossier personnel modifiés pendant la comparaison, le coordinateur n'a trouvé aucun autre
dégât (observation hors dépôt). Les **enregistrements** de la campagne sont intacts (registre de campagne, résultats et flux sont dans le dossier d'état).
**L'influence de l'incident sur l'issue des tentatives jouées après 10:03:46 est INCONNUE**, non « sans effet » : les bras
tournent avec le vrai HOME et les tests de ces bases peuvent lire le vrai registre par le même défaut d'isolation, ce qui peut
changer un résultat de test. Tentatives concernées (horodatages du registre versé) : L PR 42 tour 0 (l'incident lui-même,
10:03:21 à 10:05:18), L PR 42 tours 1 et 2, E PR 42, toutes les tentatives de PR 33 (A, L, E) et de PR 37 (A, L, E), soit 16
sessions d'implémentation et de revue ; les tentatives de 09:15 à 09:58 sont antérieures. Les tests protégés de PR 42 portent
précisément sur le registre (`test_registry_sharing.py`). La conclusion mécanique (`keep_cloud_insufficient_evidence`) est
inchangée, aucune tentative n'est reclassée ni rejouée. Pour mémoire, PR 26 et PR 38 (jouées avant) ont les mêmes comptes de
tests sur leurs 9 tentatives. L'exposition de l'exécution des tests cachés par le juge lui-même est INCONNUE : le document du
corpus (`pat-19-corpus-v1.md`) dit que le juge les lance dans un environnement réduit, avec `HOME` et `TMPDIR` pointant vers un
dossier temporaire neuf et aucune variable `FOUNDRY_*`, ce qui est une garantie de conception, non une vérification sur ces
exécutions.

Ce que cela change : aucun bras cloud ne doit être relancé avant qu'il ne reçoive un état Foundry isolé (`FOUNDRY_DATA` dans son
dossier d'essai) ; le correctif est le ticket PAT-120 (créé le 2026-10-07 : `FOUNDRY_DATA` isolé par exécution cloud, empreinte du registre vérifiée autour de chaque exécution cloud, sauvegarde par l'opérateur), pas ce diff. Plus largement, l'incident montre
que la limite « bras cloud non confinés » n'est pas théorique sur ce corpus : exécuter les tests d'une ancienne base du dépôt
peut écrire hors du bundle.

## Les trois verdicts (PAT-ADR-0015)

- **Compatibilité** (bras L) : **pass** (rapport mécanique).
- **Qualité** (bras L) : **indisponible** (tâches indécidées ; aucune tâche acceptée dans A ni dans L).
- **Économie** (bras L) : **indisponible** (pas de ratio par tâche acceptée).
- E : informatif seulement. Aucune promotion. Conséquence par la règle gelée : `keep_cloud_insufficient_evidence`.

## Écarts et limites

- **Chargements de modèles** autorisés en bloc par le mainteneur le 2026-10-07, non confirmés un par un (écart à « chaque
  chargement confirmé »).
- **Quatrième variable, le contexte** : qwen3.6 a tourné à 262 144 et qwen3-coder à 65 536 (observé, voir « Conditions »).
  Le classement entre les deux candidats et la comparaison v2 vers v3 (v2 : contexte >= 65 536, valeur observée non relue ici)
  portent donc sur quatre variables à la fois (budget, jeu de candidats, régime de chargement, contexte). Indice non prouvé :
  entrée moyenne du flux par étape au tamis, 34 981 tokens pour qwen3.6 (6 296 550 sur 180 étapes) contre 26 044 pour
  qwen3-coder (6 693 269 sur 257 étapes) ; nous ne savons pas si le contexte plus large a joué.
- **Limites connues du protocole** : le tamis est ajusté sur ses six tâches ; le bras A s'exécute avant l'exploration locale L
  de la même tâche (ordre non contrebalancé).
- **Définition des tokens premium non pondérée** : les lectures de cache comptent comme n'importe quel token, ce qui pèse sur
  les explorations Haiku (majoritairement des lectures de cache) ; un autre barème changerait les ratios, non calculé.
- **Contamination** : 7 drapeaux de forme unique, non arbitrés ; effet sur la conclusion inconnu si elle était reclassée
  (non calculé, non demandé).
- **Ce que la comparaison n'a pas pu mesurer** : l'effet aval de l'exploration, car A (référence) n'a aucune tâche acceptée
  (3 refusées par le juge à tous les tours, 3 jamais jugées par contamination). Sur PR 33 le juge a accepté L et E ; sur PR 25
  il a discriminé (E accepté, A et L refusés). Recalculé : sur PR 26 les 9 tentatives (3 bras x 3 tours) ont le même compte
  (0 passé, 1 échoué), et sur PR 38 les 9 aussi (28 passés, 8 échoués). Lecture (hypothèse) : ces comptes strictement
  identiques rendent un défaut de l'instrument plausible sur ces deux tâches ; non établi. L'acceptation de chaque tâche repose
  sur les tests protégés du PR d'origine (un ou deux nœuds, `protected.entries` du manifeste et du cliché). Ce n'est pas un
  résultat en faveur du local ou du cloud.
- Six tâches ne sont pas une preuve générale ; une seule machine, un seul moteur de local (MLX), une seule exécution par
  couple tâche / bras.
- Les débits de génération et de préremplissage ne sont pas exposés par le flux : inconnus.

## Pièces versionnées

Dans [`pat-19-runs/x3screen-1/`](pat-19-runs/x3screen-1/), copiées telles quelles : `ledger-pat-19-x3screen-1.jsonl`,
`results-pat-19-x3screen-1.jsonl`, `report-pat-19-x3screen-1.json`, `envelope.json`, les deux
`operator-screen-*.log`, `streams-manifest.json`. Dans [`pat-19-runs/x3compare-1/`](pat-19-runs/x3compare-1/) :
`ledger-pat-19-x3compare-1.jsonl`, `results-pat-19-x3compare-1.jsonl`, `report-pat-19-x3compare-1.json`, `envelope.json`,
`operator-compare-*.log`, `streams-manifest.json`. Les flux d'événements omp et cloud bruts ne sont pas versionnés (12
flux, environ 37 Mo, et 57 flux, environ 22 Mo : ils embarquent des contenus du dépôt) ; ils restent sur la machine du
mainteneur et `streams-manifest.json` donne leur sha256 et leur taille. Tous les chiffres de ce document sont recalculés à
partir des fichiers versés, hors les observations hors dépôt signalées dans le texte (machine dédiée, contenu de la racine
de travail, autorisation des chargements).

## Statut documentaire (R5)

Ce document et les pièces ci-dessus sont les seuls artefacts ajoutés ; un lien est ajouté dans la section « Protocole v3 »
de `pat-19-launcher-v1.md`, une ligne dans le CHANGELOG. Aucun verbe ni option de `foundry_cli.py`, clé de configuration,
constante publique ou table de routage n'a changé ; protocole v3 et configuration v3 inchangés ; aucun code.
