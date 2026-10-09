# PAT-19 — Où part le travail premium : répartition par rôle, par classe de tokens et par modèle, lecture pondérée (PAT-129)

PAT-129. Cadre : PAT-ADR-0015 (la règle gelée d'une campagne n'est ni recalculée ni requalifiée ; une donnée absente n'est jamais
zéro ; preuve insuffisante = conserver le cloud), FOUNDRY-ADR-0015 (coût lu dans les journaux de session ; grille de prix datée, avec
source, date de relevé et validité), FOUNDRY-ADR-0019 (comparaison bornée). Pièces lues : les campagnes v4
([`pat-19-exploration-results-v4.md`](pat-19-exploration-results-v4.md)) et v5
([`pat-19-exploration-results-v5.md`](pat-19-exploration-results-v5.md)), le bilan
[`pat-19-local-first-bilan.md`](pat-19-local-first-bilan.md), le lanceur [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md).
**Aucune règle, aucun protocole, aucune configuration, aucun résultat ni verdict gelé n'est modifié, recalculé ou requalifié par ce
document.** Rien n'a été exécuté contre un modèle : ni `claude`, ni `lms`, ni bras cloud, ni mode de campagne ; l'outil lit des
fichiers.

Légende des marques. **[fichiers]** : recalculé sur les fichiers versés (`pat-19-runs/x4compare-1/`, `pat-19-runs/x5compare-1/`).
**[flux]** : lu sur les transcriptions brutes des sessions cloud et sur les journaux de session de l'hôte, qui restent hors dépôt et ne
peuvent pas être revérifiés depuis le dépôt ; seuls des agrégats en sont versés (jamais un chemin, une commande, un extrait ni une
transcription). **[code]** : calculé par l'outil du dépôt (`foundry.cost_breakdown`), couvert par ses tests. **[coord.]** : relevé du
coordinateur, hors dépôt. **[hypothèse]** : lecture non établie. **[inconnu]** : non su. Un chiffre sans marque est un décompte
[fichiers].

## Résumé

- **Les totaux recalculés égalent ceux des rapports versés**, pour les deux bras des deux campagnes (jetons de facturation totaux et
  répartition par modèle) [fichiers][code]. Aucun écart avec les rapports ni avec le document de résultats v5 (voir « Accords et
  écarts »).
- **Où part le travail premium, en tokens (somme non pondérée de la règle)**. v5, bras A (11 433 953) : implémenteur 39,3 %, correcteur
  31,1 %, relecteur Opus 29,6 % ; bras L (7 874 314) : 27,9 %, 37,7 %, 34,3 %. Les lectures de cache font 88 % (A) et 86 % (L) des
  tokens ; la sortie 2 %. v4, bras A (4 134 697) : 50,0 %, 38,2 %, 11,8 % ; bras L (2 870 126) : 37,1 %, 45,8 %, 17,1 %.
  L'explorateur du bras L est local : zéro token premium, il n'apparaît dans aucune de ces parts.
- **Le même travail pondéré par des prix de liste d'API** (poids, pas une facture) change l'image. Avec les écritures de cache à 1 h (la borne qui correspond aux transcriptions : 100 % des écritures de cache observées sont à 1 h) : l'écriture de cache pèse 56 à 59 % du poids total, la sortie 30 à 32 %, la lecture de cache 10 à 13 % (v4 et v5, deux bras). Avec les écritures à 5 min (contrefactuel) : 44 à 47 %, 39 à 40 % et 13 à 16 %. Le relecteur Opus, 30 à 34 % des tokens en v5, pèse **51 à 55 %** du poids en v5 (51,4 à 53,5 % à 1 h, 51,8 à 54,5 % à 5 min). Avec le prix de lecture de cache de Sonnet 5.5 que reproduit l'hôte (0,20 au lieu de 0,10, voir plus bas), cette part est de **48 % (A) et 51 % (L)** en v5, et la lecture de cache y pèse 15 à 23 %. [code]
- **La lecture pondérée du rapport L sur A, par tâche acceptée, sur l'ensemble apparié D de la v5, hors règle** : **1,032** (écritures de
  cache à 5 min ou à 1 h : 1,0319 ou 1,0318), contre 0,8579 non pondéré. Autrement dit, pondéré, L n'est pas en dessous de A sur D ;
  il est au-dessus de 3 %. Avec 5 tâches acceptées par bras et une marge de cet ordre, **ce rapport ne distingue pas L de A** ; il
  montre surtout que la conclusion « moins de tokens » dépend de ce qu'un token pèse. Le verdict gelé (`keep_cloud`, rapport 0,8579 pour un
  seuil de 0,85) reste celui du rapport. Aucun gain de facture n'est annoncé, aucune perte non plus : le forfait est un abonnement.
- **Exploration du dépôt dans les sessions cloud de l'implémenteur et du correcteur** [flux]. Part des appels d'outil qui sont des lectures ou
  recherches (définition précise plus bas) : v5, A : implémenteur 51,7 %, correcteur 53,0 % ; L : 63,3 % et 46,6 %. Sur D : A 53,0 %
  et 52,1 % ; L 63,3 % et 46,6 %. Par session de l'implémenteur : 7,1 appels (A) contre 6,2 (L) sur D. Les sessions de l'implémenteur du bras L, qui reçoit le rapport de l'explorateur local, comptent encore 6,2 appels de lecture ou recherche en moyenne sur D (contre 7,1 pour le bras A)** ; les données ne disent pas pourquoi (voir « Ce qui reste inconnu » et « Limites »).
- **Poids direct des appels API purement d'exploration, par rôle, sous hypothèse** (hypothèse : ces appels ne coûteraient rien ; le poids est celui de leurs seuls tokens propres) : v5, bras A, pondéré, écritures à 1 h : implémenteur 11,6 % du poids du bras, correcteur 12,8 %, relecteur 7,9 %, soit **32,3 %** pour les trois ; **20,1 %** si l'on retire le premier appel de chaque session (qui écrit dans le cache le prompt du système et de la tâche). Bras L : 33,6 % et 20,0 %. **Ce n'est pas un plafond du gain d'une réduction** : le chiffre est incomplet dans les deux sens (il ne compte pas ce que le résultat d'une lecture coûte ensuite en cache relu, il ne compte pas les lectures cachées dans `bash_other` ni les appels mixtes classés en action, ce qui le sous-estime ; il traite comme exploration une lecture qui précède une modification du même fichier, ce qui le surestime), et supprimer ces appels n'est pas une mesure disponible. Il dit seulement quelle part du poids est portée directement par ces appels, aujourd'hui, dans ces deux campagnes. Il ne vaut ni prévision ni gain de facture.
- **Écriture de cache : la scission 5 min / 1 h est connue ici** [flux] : dans les 104 sessions cloud des deux campagnes, 100 % des tokens
  d'écriture de cache sont des écritures à 1 h, aucune à 5 min. Les enregistrements versés n'ont qu'une classe d'écriture ; les tableaux
  donnent donc les deux bornes, et la borne à 1 h est celle qui correspond aux transcriptions.
- **Un désaccord à connaître sur le prix de la lecture de cache de Sonnet 5.5** [flux][coord.] : la page de prix relevée par le coordinateur
  donne 0,10 USD par million, alors que le coût de liste que l'hôte écrit lui-même dans les transcriptions se reproduit exactement avec 0,20 (et
  les autres prix identiques). Opus 5.5 se reproduit exactement avec la page. La pondération principale utilise la page (consigne du coordinateur) ; la sensibilité est donnée pour les parts qui en dépendent (section « Lecture pondérée de L sur A », option `--override-rate`).

## Méthode

**Outil** [code] : `python3 -m foundry.cost_breakdown` (module `plugins/foundry/tooling/foundry/cost_breakdown.py`, tests dans
`tests/test_cost_breakdown.py`). Lecture seule. Entrées : `results-<campagne>.jsonl` et `ledger-<campagne>.jsonl` (à côté), optionnellement
`--report report-<campagne>.json` (les totaux recalculés y sont comparés, code de sortie 1 en cas d'écart) ; optionnellement
`--streams-dir` (transcriptions brutes, hors dépôt) et `--session-logs-dir` (journaux de session de l'hôte, pour les tokens de sortie
exacts par appel API) ; `--grid` ; `--out` (n'écrase jamais). `--override-rate MODELE:CLE=VALEUR` ajoute au résultat toute la lecture refaite avec un tarif remplacé (sensibilité). Il n'affiche que des agrégats. Il réutilise les enregistrements du lanceur ;
il ne recalcule aucun verdict.

**Répartition par rôle** [fichiers] : un enregistrement porte les tokens par rôle (`by_role`) et par modèle (`by_model`), pas les deux à la fois.
Le modèle d'un rôle est déduit sans deviner : un seul modèle dans l'enregistrement, ou une seule affectation des rôles aux modèles qui
reproduise exactement les compteurs de chaque modèle ; sinon la pondération du rôle est `unavailable`. Dans v4 et v5 toutes les cellules se
résolvent (implémenteur et correcteur : Sonnet 5.5 ; relecteur : Opus 5.5). Les tokens de raisonnement sont montrés à part et sont un sous-ensemble
de la sortie (jamais ajoutés).

**Grille de prix** [code] : `plugins/foundry/tooling/foundry/pricing-breakdown-v1.json`, nouvelle grille versionnée à côté de `pricing-v1.json`
(laissé intact : ses tests affirment que les modèles 5.5 n'y ont pas de prix, et son schéma n'a qu'une classe d'écriture de cache). Prix de liste
d'API, USD par million de tokens, relevés par le coordinateur le 2026-10-08 (UTC) sur
`https://platform.claude.com/docs/en/about-claude/pricing` [coord.] :

| Modèle | Entrée | Écriture 5 min | Écriture 1 h | Lecture de cache | Sortie |
| --- | --- | --- | --- | --- | --- |
| Opus 5.5 | 4 | 5 | 8 | 0,20 | 20 |
| Sonnet 5.5 | 2 | 2,50 | 4 | 0,10 | 10 |
| Haiku 5.5, prompt jusqu'à 100 000 tokens | 0,10 | 0,125 | 0,20 | 0,01 | 0,50 |
| Haiku 5.5, prompt au-delà | 0,50 | 0,625 | 1 | 0,05 | 2,50 |
| Haiku 4.5 (relevé, non repris dans la grille) | 1 | 1,25 | 2 | 0,10 | 5 |

Haiku 4.5 : les prix relevés concordent avec l'entrée `haiku-4.5` de `pricing-v1.json` pour les classes qu'elle porte (entrée, lecture, écriture à 5 min, sortie), mais il n'est pas repris dans la nouvelle grille : v4 et v5 ne l'ont pas utilisé, et FOUNDRY-ADR-0015 refuse deux fenêtres qui se recouvrent pour un même hôte et modèle ; un test vérifie qu'il reste servi par la seule `pricing-v1.json`. Haiku 5.5 : son prix dépend de la
longueur du prompt de chaque requête, que les enregistrements ne portent pas ; l'outil le rend `unavailable` (v4 et v5 n'ont pas utilisé Haiku 5.5 ;
v3 non reprise ici, hors du périmètre demandé). **Validité** : la date de sortie des modèles 5.5 n'est pas consignée dans le dépôt ;
`effective_from` est la plus ancienne date où le dépôt les montre utilisés (2026-10-05, notes des configurations de campagne), et le prix
est **supposé inchangé** entre cette date et le relevé du 2026-10-08 [hypothèse, non vérifiée]. Pourquoi pas la date de relevé : v4 a tourné
le 2026-10-07 (jour UTC du registre), avant le relevé ; avec `effective_from` = 2026-10-08, v4 serait entièrement `unavailable`. Un modèle sans entrée
valide à la date de la session reste `unavailable`, jamais estimé. `effective_to` : 2026-12-31 (même convention que `pricing-v1.json`).

**Pondération** [code] : le coût pondéré d'un jeu de tokens est la somme, par classe, des tokens fois le prix du modèle à la date de la
session (premier lancement de l'enregistrement, jour UTC du registre). Comme l'enregistrement n'a qu'une classe d'écriture, deux
colonnes : toutes les écritures à 5 min (borne basse), toutes à 1 h (borne haute). **Ce sont des poids pour lire une répartition, jamais une
facture : le forfait est un abonnement, aucune économie ni aucun coût en dollars n'est annoncé.**

**Exploration du dépôt** [flux][code]. Unité : l'appel d'outil (`tool_use`), classé par `classify_tool_use` :

- `explore_read` : outil `Read` ; `explore_search` : outils `Grep` / `Glob` (non utilisés dans ces campagnes : les bras n'avaient que `Bash`,
  `Edit`, `Read`, `Write`) ;
- `explore_bash` : appel `Bash` fait uniquement de commandes simples de lecture ou de recherche : `cat`, `head`, `tail`, `grep` (et
  `egrep`/`fgrep`), `rg`, `ls`, `find` (sans `-exec`/`-delete`/`-ok`/`-fprint…`), `sed` (avec `-n`, jamais `-i`), au moins une, composées avec des filtres
  neutres (`cd`, `pwd`, `wc`, `sort`, `uniq`, `cut`, `tr`, `echo`, `printf`, `true`) par `&&`, `||`, `;`, `|` ou saut de ligne ; pas de
  substitution de commande, de document ici, ni de redirection vers un fichier (`2>&1` et `> /dev/null` tolérées) ;
- `bash_other` : tout autre `Bash`, y compris ce qu'on ne sait pas lire avec certitude (guillemet ouvert, substitution…), `edit` : `Edit`/`Write`,
  `other` : le reste (un nom d'outil inconnu).

Le décompte d'exploration est donc **une borne basse** des lectures de dépôt (un script Python qui lit des fichiers, ou `pytest`, est `bash_other`).
Un appel `Read` d'un fichier qu'on va ensuite modifier compte comme exploration : la mesure ne sait pas séparer lire pour comprendre et lire pour
modifier.

**Attribution de tokens** [flux]. L'unité est l'appel API (un identifiant de message de l'assistant ; les blocs et les évènements dupliqués sont
fusionnés). Un appel est « purement d'exploration » si tous ses `tool_use` sont des lectures ou recherches (et au moins un) ; « d'action » s'il en a un
autre ; sans outil (réponse finale) sinon. Les compteurs d'entrée, de lecture et d'écriture de cache de chaque appel viennent du flux de la campagne (leur somme
par session égale le compteur final de la session : vérifié pour les 104 sessions) ; les tokens de sortie par appel ne sont pas fiables dans le flux
(valeurs partielles) et viennent du journal de session de l'hôte, apparié au flux **par identifiant de message** (même ensemble d'identifiants et somme égale au compteur final : vérifié pour les 104 sessions ; si les identifiants ou le total diffèrent, ou si un compteur est absent, l'attribution du rôle est `unavailable`, jamais approchée). Le lecteur du journal de l'hôte n'extrait que l'identifiant de message et son compteur de sortie (FOUNDRY-ADR-0015) ; les appels d'outil ne sont lus que dans les flux de la campagne. Aucune
estimation n'est donc nécessaire : l'attribution de tokens est **possible à la granularité de l'appel API** pour ces 104 sessions. Ce qu'elle ne mesure pas : le coût que le
résultat d'une lecture continue d'imposer dans les appels suivants, où il reste dans le contexte relu en cache [inconnu].

## Résultats par campagne, bras et rôle

Les tableaux sont des lectures [code] des fichiers versés ; les agrégats complets sont dans
[`pat-19-cost-breakdown-v1-x4compare-1.json`](pat-19-cost-breakdown-v1-x4compare-1.json) et
[`pat-19-cost-breakdown-v1-x5compare-1.json`](pat-19-cost-breakdown-v1-x5compare-1.json). « Pondéré » : USD de liste en poids, écritures de cache
à 5 min / à 1 h. Les parts pondérées sont des parts du poids du bras. Le rôle `explorer` est local : aucun token premium.

### v4 (`pat-19-x4compare-1`, 6 tâches, 2026-10-07)

Bras A : 16 exécutions cloud, 1 tâche acceptée ; bras L : 14 exécutions, 0 acceptée (décision gelée : `inconclusive`). Les deux bras couvrent les 6 tâches.

**Bras A** : 4 134 697 tokens de facturation.

| Rôle | Entrée | Lecture de cache | Écriture de cache | Sortie (dont raisonnement) | Total | Part des tokens | Pondéré, écritures 5 min (USD) | Pondéré, écritures 1 h (USD) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| implementer | 176 | 1 866 353 | 167 309 | 33 006 (5 258) | 2 066 844 | 50,0 % | 0,94 | 1,19 | 36,4 % / 36,4 % |
| corrector | 176 | 1 389 894 | 159 837 | 31 007 (9 926) | 1 580 914 | 38,2 % | 0,85 | 1,09 | 33,0 % / 33,4 % |
| reviewer | 38 | 402 809 | 65 003 | 19 089 (6 109) | 486 939 | 11,8 % | 0,79 | 0,98 | 30,6 % / 30,2 % |
| explorer | 0 | 0 | 0 | 0 (0) | 0 | 0 % | 0,00 | 0,00 | — (exploration locale : aucun token premium) |
| **Total** | 390 | 3 659 056 | 392 149 | 83 102 (21 293) | 4 134 697 | 100 % | 2,57 | 3,26 | |

| Classe | Tokens | Part des tokens | USD pondérés (5 min / 1 h pour l'écriture) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- |
| entrée | 390 | 0,0 % | 0,00 | 0,0 % / 0,0 % |
| lecture de cache | 3 659 056 | 88,5 % | 0,41 | 15,8 % / 12,5 % |
| écriture de cache | 392 149 | 9,5 % | 1,14 / 1,83 | 44,4 % / 56,1 % |
| sortie | 83 102 | 2,0 % | 1,02 | 39,7 % / 31,4 % |

| Modèle | Tokens | Pondéré 5 min (USD) | Pondéré 1 h (USD) |
| --- | --- | --- | --- |
| claude-opus-5-5 | 486 939 | 0,79 | 0,98 |
| claude-sonnet-5-5 | 3 647 758 | 1,78 | 2,28 |

**Bras L** : 2 870 126 tokens de facturation.

| Rôle | Entrée | Lecture de cache | Écriture de cache | Sortie (dont raisonnement) | Total | Part des tokens | Pondéré, écritures 5 min (USD) | Pondéré, écritures 1 h (USD) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| implementer | 102 | 924 565 | 118 318 | 23 202 (4 068) | 1 066 187 | 37,1 % | 0,62 | 0,80 | 27,5 % / 27,6 % |
| corrector | 142 | 1 138 796 | 145 693 | 28 923 (7 943) | 1 313 554 | 45,8 % | 0,77 | 0,99 | 34,1 % / 34,1 % |
| reviewer | 32 | 390 889 | 80 140 | 19 324 (5 511) | 490 385 | 17,1 % | 0,87 | 1,11 | 38,4 % / 38,3 % |
| explorer | 0 | 0 | 0 | 0 (0) | 0 | 0 % | 0,00 | 0,00 | — (exploration locale : aucun token premium) |
| **Total** | 276 | 2 454 250 | 344 151 | 71 449 (17 522) | 2 870 126 | 100 % | 2,25 | 2,89 | |

| Classe | Tokens | Part des tokens | USD pondérés (5 min / 1 h pour l'écriture) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- |
| entrée | 276 | 0,0 % | 0,00 | 0,0 % / 0,0 % |
| lecture de cache | 2 454 250 | 85,5 % | 0,28 | 12,6 % / 9,8 % |
| écriture de cache | 344 151 | 12,0 % | 1,06 / 1,70 | 47,1 % / 58,7 % |
| sortie | 71 449 | 2,5 % | 0,91 | 40,3 % / 31,4 % |

| Modèle | Tokens | Pondéré 5 min (USD) | Pondéré 1 h (USD) |
| --- | --- | --- | --- |
| claude-opus-5-5 | 490 385 | 0,87 | 1,11 |
| claude-sonnet-5-5 | 2 379 741 | 1,39 | 1,78 |

### v5 (`pat-19-x5compare-1`, 12 tâches, 2026-10-08)

Bras A : 39 exécutions cloud, 6 acceptées ; bras L : 35, 5 acceptées (décision gelée : `keep_cloud`, rapport 0,8579 pour un seuil de 0,85, sur
l'ensemble apparié D de 10 tâches). Les totaux des 12 tâches ne se comparent pas entre bras : L n'a rien dépensé sur PR 42 et PR 33 (explorations
contaminées).

**Bras A** : 11 433 953 tokens de facturation.

| Rôle | Entrée | Lecture de cache | Écriture de cache | Sortie (dont raisonnement) | Total | Part des tokens | Pondéré, écritures 5 min (USD) | Pondéré, écritures 1 h (USD) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| implementer | 316 | 4 054 337 | 366 345 | 73 406 (9 632) | 4 494 404 | 39,3 % | 2,06 | 2,61 | 24,9 % / 24,8 % |
| corrector | 334 | 3 105 913 | 385 993 | 63 876 (14 319) | 3 556 116 | 31,1 % | 1,92 | 2,49 | 23,2 % / 23,8 % |
| reviewer | 190 | 2 918 177 | 374 072 | 90 994 (33 789) | 3 383 433 | 29,6 % | 4,27 | 5,40 | 51,8 % / 51,4 % |
| explorer | 0 | 0 | 0 | 0 (0) | 0 | 0 % | 0,00 | 0,00 | — (exploration locale : aucun token premium) |
| **Total** | 840 | 10 078 427 | 1 126 410 | 228 276 (57 740) | 11 433 953 | 100 % | 8,25 | 10,50 | |

| Classe | Tokens | Part des tokens | USD pondérés (5 min / 1 h pour l'écriture) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- |
| entrée | 840 | 0,0 % | 0,00 | 0,0 % / 0,0 % |
| lecture de cache | 10 078 427 | 88,1 % | 1,30 | 15,8 % / 12,4 % |
| écriture de cache | 1 126 410 | 9,9 % | 3,75 / 6,00 | 45,5 % / 57,2 % |
| sortie | 228 276 | 2,0 % | 3,19 | 38,7 % / 30,4 % |

| Modèle | Tokens | Pondéré 5 min (USD) | Pondéré 1 h (USD) |
| --- | --- | --- | --- |
| claude-opus-5-5 | 3 383 433 | 4,27 | 5,40 |
| claude-sonnet-5-5 | 8 050 520 | 3,97 | 5,10 |

**Bras L** : 7 874 314 tokens de facturation.

| Rôle | Entrée | Lecture de cache | Écriture de cache | Sortie (dont raisonnement) | Total | Part des tokens | Pondéré, écritures 5 min (USD) | Pondéré, écritures 1 h (USD) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| implementer | 194 | 1 915 186 | 242 177 | 42 911 (5 173) | 2 200 468 | 27,9 % | 1,23 | 1,59 | 18,6 % / 18,9 % |
| corrector | 276 | 2 543 408 | 369 026 | 59 605 (17 735) | 2 972 315 | 37,7 % | 1,77 | 2,33 | 26,9 % / 27,6 % |
| reviewer | 172 | 2 319 191 | 300 516 | 81 652 (27 479) | 2 701 531 | 34,3 % | 3,60 | 4,50 | 54,5 % / 53,5 % |
| explorer | 0 | 0 | 0 | 0 (0) | 0 | 0 % | 0,00 | 0,00 | — (exploration locale : aucun token premium) |
| **Total** | 642 | 6 777 785 | 911 719 | 184 168 (50 387) | 7 874 314 | 100 % | 6,60 | 8,42 | |

| Classe | Tokens | Part des tokens | USD pondérés (5 min / 1 h pour l'écriture) | Part pondérée (5 min / 1 h) |
| --- | --- | --- | --- | --- |
| entrée | 642 | 0,0 % | 0,00 | 0,0 % / 0,0 % |
| lecture de cache | 6 777 785 | 86,1 % | 0,91 | 13,8 % / 10,8 % |
| écriture de cache | 911 719 | 11,6 % | 3,03 / 4,85 | 45,9 % / 57,6 % |
| sortie | 184 168 | 2,3 % | 2,66 | 40,3 % / 31,6 % |

| Modèle | Tokens | Pondéré 5 min (USD) | Pondéré 1 h (USD) |
| --- | --- | --- | --- |
| claude-opus-5-5 | 2 701 531 | 3,60 | 4,50 |
| claude-sonnet-5-5 | 5 172 783 | 3,00 | 3,92 |

## Lecture pondérée de L sur A, hors règle

**Hors règle, sans valeur de décision.** Le verdict de chaque campagne est celui du rapport versé ; il n'est ni recalculé ni requalifié.

**v5, ensemble apparié D (10 tâches, 5 acceptées par bras)** [code] : sommes sur D, A 9 178 695 tokens et L 7 874 314 (identiques à `paired_rule.premium_on_paired`).
Par rôle sur D, en tokens : A, implémenteur 3 457 293, correcteur 3 209 177, relecteur 2 512 225 ; L, 2 200 468, 2 972 315, 2 701 531.

| Lecture | A (sur D) | L (sur D) | Par tâche acceptée A | Par tâche acceptée L | Rapport L / A par tâche acceptée |
| --- | --- | --- | --- | --- | --- |
| Tokens, somme non pondérée (la lecture de la règle) | 9 178 695 | 7 874 314 | 1 835 739 | 1 574 863 | **0,8579** (celui du rapport) |
| Pondéré, écritures à 5 min (USD de liste, poids) | 6,40 | 6,60 | 1,28 | 1,32 | **1,0319** |
| Pondéré, écritures à 1 h (USD de liste, poids) | 8,16 | 8,42 | 1,63 | 1,68 | **1,0318** |

Sur D, par classe (pondéré, écritures à 1 h, USD de liste) : A, lecture de cache 1,03, écriture 4,70, sortie 2,43 ; L, 0,91, 4,85, 2,66. L lit moins de cache que A (0,91 contre 1,03) mais écrit un peu plus (4,85 contre 4,70) et produit un peu plus de sortie (2,66 contre 2,43) ; le relecteur Opus (poids élevé par token) est plus grand en L (2 701 531 tokens contre 2 512 225). Ce sont des constats chiffrés, pas des causes.

**Sensibilité au prix de la lecture de cache de Sonnet 5.5** [flux][code] : avec 0,20 au lieu de 0,10 (le prix qui reproduit le coût de liste de l'hôte), le rapport
devient 1,0129 (écritures à 1 h) et 1,0083 (à 5 min). Même sens, même ordre de grandeur pour ce rapport. En revanche les parts qui dépendent de ce prix bougent, et sont recalculées par l'outil (`--override-rate claude-sonnet-5-5:cache_read=0.20`, bloc `sensitivity` des fichiers d'agrégats) :

v4, avec Sonnet 5.5 à 0,20 :

| Bras | Poids total, USD de liste (5 min / 1 h) | Implémenteur (5 min / 1 h) | Correcteur | Relecteur Opus | Lecture de cache | Écriture de cache | Sortie |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 2,90 / 3,58 | 38,7 % / 38,3 % | 34,1 % / 34,3 % | 27,2 % / 27,4 % | 25,3 % / 20,4 % | 39,4 % / 51,0 % | 35,3 % / 28,5 % |
| L | 2,46 / 3,10 | 29,0 % / 28,8 % | 35,8 % / 35,5 % | 35,2 % / 35,7 % | 20,0 % / 15,9 % | 43,1 % / 54,8 % | 36,9 % / 29,3 % |

v5, avec Sonnet 5.5 à 0,20 :

| Bras | Poids total, USD de liste (5 min / 1 h) | Implémenteur (5 min / 1 h) | Correcteur | Relecteur Opus | Lecture de cache | Écriture de cache | Sortie |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 8,96 / 11,21 | 27,5 % / 26,9 % | 24,8 % / 25,0 % | 47,7 % / 48,1 % | 22,5 % / 18,0 % | 41,9 % / 53,5 % | 35,6 % / 28,5 % |
| L | 7,05 / 8,86 | 20,1 % / 20,1 % | 28,8 % / 29,1 % | 51,1 % / 50,8 % | 19,2 % / 15,3 % | 43,0 % / 54,7 % | 37,7 % / 30,0 % |

Poids direct des appels purement d'exploration avec Sonnet 5.5 à 0,20 (v4 puis v5) :

| Bras | Rôle | Part du rôle dans le poids du bras (5 min / 1 h) | Poids direct des appels purement d'exploration, en part du poids du bras (5 min / 1 h) | Idem hors premier appel de chaque session (5 min / 1 h) | Idem en part des tokens du bras |
| --- | --- | --- | --- | --- | --- |
| A | implementer | 38,7 % / 38,3 % | 17,1 % / 18,4 % | 13,4 % / 13,7 % | 19,3 % |
| A | corrector | 34,1 % / 34,3 % | 15,2 % / 16,2 % | 9,8 % / 9,6 % | 15,1 % |
| A | reviewer | 27,2 % / 27,4 % | 3,1 % / 3,2 % | 3,1 % / 3,2 % | 1,5 % |
| A | **trois rôles** | 100 % | 35,4 % / 37,8 % | 26,3 % / 26,6 % | 35,9 % |
| L | implementer | 29,0 % / 28,8 % | 14,7 % / 15,6 % | 11,0 % / 11,2 % | 16,4 % |
| L | corrector | 35,8 % / 35,5 % | 14,7 % / 15,9 % | 9,1 % / 9,1 % | 16,7 % |
| L | reviewer | 35,2 % / 35,7 % | 3,9 % / 4,3 % | 3,9 % / 4,3 % | 1,3 % |
| L | **trois rôles** | 100 % | 33,4 % / 35,8 % | 24,1 % / 24,6 % | 34,4 % |

| Bras | Rôle | Part du rôle dans le poids du bras (5 min / 1 h) | Poids direct des appels purement d'exploration, en part du poids du bras (5 min / 1 h) | Idem hors premier appel de chaque session (5 min / 1 h) | Idem en part des tokens du bras |
| --- | --- | --- | --- | --- | --- |
| A | implementer | 27,5 % / 26,9 % | 11,4 % / 12,2 % | 7,7 % / 7,6 % | 15,5 % |
| A | corrector | 24,8 % / 25,0 % | 11,9 % / 13,0 % | 6,2 % / 6,1 % | 12,6 % |
| A | reviewer | 47,7 % / 48,1 % | 7,2 % / 7,4 % | 7,2 % / 7,4 % | 4,6 % |
| A | **trois rôles** | 100 % | 30,5 % / 32,6 % | 21,2 % / 21,1 % | 32,7 % |
| L | implementer | 20,1 % / 20,1 % | 10,7 % / 11,7 % | 6,9 % / 7,0 % | 14,2 % |
| L | corrector | 28,8 % / 29,1 % | 12,1 % / 13,4 % | 5,3 % / 5,1 % | 12,4 % |
| L | reviewer | 51,1 % / 50,8 % | 8,8 % / 8,7 % | 8,8 % / 8,7 % | 7,2 % |
| L | **trois rôles** | 100 % | 31,7 % / 33,8 % | 21,1 % / 20,8 % | 33,8 % |

Lecture : la part du relecteur Opus passe, en v5, de 51–52 % (A) et 54–55 % (L) à 48 % (A) et 51 % (L) ; en v4, de 30–31 % (A) et 38 % (L) à 27 % (A) et 35–36 % (L) ; le poids direct des trois rôles reste à 30 à 34 % en v5 (21 % hors premier appel). Les ordres de grandeur et le sens des constats ne changent pas ; les valeurs sont donc à lire avec cet intervalle.

**Contrôle contre le coût de liste que l'hôte écrit dans les transcriptions** [flux] (`total_cost_usd`, `costBasis` « list ») : v5, bras A 11,21 USD, bras L 8,86 ; v4, A 3,58, L 3,10.
Avec la grille de la page, écritures à 1 h : v5 A 10,50 et L 8,42 ; v4 A 3,26 et L 2,89. L'écart est exactement la lecture de cache de Sonnet × 0,10 USD par million ; avec 0,20 pour la lecture de cache de Sonnet 5.5 (et les prix de la page pour le reste), le coût de liste de l'hôte se reproduit à 10^-6 USD près **dans chacune des 104 sessions** (Opus compris, avec les prix de la page).

**v4 (6 tâches)** : L n'a accepté aucune tâche, A une ; le rapport par tâche acceptée est `unavailable` (comme dans le rapport gelé). Pour mémoire seulement, sur les 6 tâches : pondéré, L / A vaut
0,876 (écritures à 5 min) et 0,887 (à 1 h), contre 0,694 en tokens bruts ; ce n'est pas un rapport par tâche acceptée et il ne se compare pas au seuil 0,85.

**Lecture, sans cause** : la pondération déplace le poids vers l'écriture de cache, la sortie et le relecteur Opus, et L ne dépense pas moins que A sur ces postes
sur D. Cela ne dit pas que L est plus cher ni moins bon : 10 tâches, 5 acceptées par bras, des ensembles de tâches acceptées qui diffèrent sur 4 des 10,
aucun essai répété.

## Exploration du dépôt dans les sessions cloud de l'implémenteur et du correcteur

[flux][code] 104 transcriptions (v4 : 30, v5 : 74), toutes avec un évènement de résultat final, toutes vérifiées contre les compteurs (voir « Méthode »). Les
explorations locales du bras L (6 en v4, 12 en v5) ne sont pas des sessions cloud et ne sont pas lues ici. Le relecteur est donné pour comparaison.

### v4

| Bras | Rôle | Sessions | Appels API | Appels d'outil | Lectures / recherches | Part des appels d'outil | Par session | Appels API purement d'exploration | Tokens de ces appels (part des tokens du rôle) | Poids direct de ces appels, USD de liste, 5 min / 1 h (part du poids du rôle) | Idem hors premier appel de la session, 5 min / 1 h (part du poids du rôle) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | implementer | 6 | 88 | 89 | 52 (Read 16, Bash 36) | 58,4 % | 8,7 | 46 sur 88 | 796 607 (38,5 %) | 0,43 / 0,59 (45,7 % / 49,8 %) | 0,32 / 0,43 (34,4 % / 35,9 %) |
| A | corrector | 8 | 88 | 89 | 49 (Read 18, Bash 31) | 55,1 % | 6,1 | 40 sur 88 | 622 877 (39,4 %) | 0,39 / 0,53 (45,9 % / 48,6 %) | 0,24 / 0,29 (27,7 % / 27,1 %) |
| A | reviewer | 2 | 19 | 20 | 5 (Read 0, Bash 5) | 25,0 % | 2,5 | 3 sur 19 | 63 869 (13,1 %) | 0,09 / 0,12 (11,4 % / 11,7 %) | 0,09 / 0,12 (11,4 % / 11,7 %) |
| L | implementer | 5 | 51 | 52 | 34 (Read 12, Bash 22) | 65,4 % | 6,8 | 28 sur 51 | 470 839 (44,2 %) | 0,32 / 0,45 (52,1 % / 55,8 %) | 0,23 / 0,31 (37,9 % / 38,7 %) |
| L | corrector | 7 | 71 | 73 | 38 (Read 20, Bash 18) | 52,1 % | 5,4 | 29 sur 71 | 479 090 (36,5 %) | 0,32 / 0,45 (42,2 % / 45,9 %) | 0,19 / 0,25 (24,5 % / 24,9 %) |
| L | reviewer | 2 | 16 | 17 | 4 (Read 0, Bash 4) | 23,5 % | 2,0 | 2 sur 16 | 38 707 (7,9 %) | 0,10 / 0,13 (11,2 % / 12,0 %) | 0,10 / 0,13 (11,2 % / 12,0 %) |

### v5

| Bras | Rôle | Sessions | Appels API | Appels d'outil | Lectures / recherches | Part des appels d'outil | Par session | Appels API purement d'exploration | Tokens de ces appels (part des tokens du rôle) | Poids direct de ces appels, USD de liste, 5 min / 1 h (part du poids du rôle) | Idem hors premier appel de la session, 5 min / 1 h (part du poids du rôle) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | implementer | 12 | 158 | 172 | 89 (Read 24, Bash 65) | 51,7 % | 7,4 | 81 sur 158 | 1 767 157 (39,3 %) | 0,87 / 1,21 (42,3 % / 46,6 %) | 0,54 / 0,70 (26,5 % / 27,1 %) |
| A | corrector | 18 | 167 | 183 | 97 (Read 46, Bash 51) | 53,0 % | 5,4 | 73 sur 167 | 1 441 729 (40,5 %) | 0,95 / 1,34 (49,4 % / 53,9 %) | 0,45 / 0,57 (23,3 % / 23,0 %) |
| A | reviewer | 9 | 95 | 107 | 29 (Read 0, Bash 29) | 27,1 % | 3,2 | 16 sur 95 | 526 224 (15,6 %) | 0,65 / 0,83 (15,2 % / 15,4 %) | 0,65 / 0,83 (15,2 % / 15,4 %) |
| L | implementer | 10 | 97 | 98 | 62 (Read 16, Bash 46) | 63,3 % | 6,2 | 57 sur 97 | 1 114 947 (50,7 %) | 0,67 / 0,95 (54,2 % / 59,4 %) | 0,40 / 0,53 (32,7 % / 33,4 %) |
| L | corrector | 17 | 138 | 146 | 68 (Read 40, Bash 28) | 46,6 % | 4,0 | 50 sur 138 | 975 511 (32,8 %) | 0,78 / 1,12 (44,1 % / 47,9 %) | 0,31 / 0,38 (17,4 % / 16,5 %) |
| L | reviewer | 8 | 86 | 99 | 32 (Read 0, Bash 32) | 32,3 % | 4,0 | 19 sur 86 | 570 756 (21,1 %) | 0,62 / 0,77 (17,2 % / 17,1 %) | 0,62 / 0,77 (17,2 % / 17,1 %) |

### v5, ensemble apparié D

| Bras | Rôle | Sessions | Appels d'outil | Lectures / recherches | Part des appels d'outil | Par session |
| --- | --- | --- | --- | --- | --- | --- |
| A | implementer | 10 | 134 | 71 | 53,0 % | 7,1 |
| A | corrector | 16 | 167 | 87 | 52,1 % | 5,4 |
| A | reviewer | 7 | 83 | 19 | 22,9 % | 2,7 |
| L | implementer | 10 | 98 | 62 | 63,3 % | 6,2 |
| L | corrector | 17 | 146 | 68 | 46,6 % | 4,0 |
| L | reviewer | 8 | 99 | 32 | 32,3 % | 4,0 |

Lectures [flux], sans cause :

- Dans les deux campagnes et les deux bras, **plus de la moitié des appels d'outil de l'implémenteur** sont des lectures ou recherches (51,7 % à 65,4 %) ; chez le correcteur, 46,6 % à 55,1 % ; chez le
  relecteur 23,5 % à 32,3 %. Aucun appel `Grep` ou `Glob` : les bras n'en disposaient pas.
- **A contre L** : l'implémenteur de L fait une part plus grande de lectures (63,3 % contre 51,7 % en v5 ; 65,4 % contre 58,4 % en v4) mais moins d'appels par session
  (v5 sur D : 6,2 contre 7,1 ; v4 : 6,8 contre 8,7). Le correcteur de L en fait moins en part et en nombre par session en v5 (46,6 % ; 4,0 contre 5,4 par session), et à peu près pareil en v4 (52,0 % ; 5,4 contre 6,1).
  Sessions différentes en nombre et en tâches (sur les 12 tâches, L compte 10 sessions d'implémenteur contre 12 ; sur D, 10 contre 10), un seul passage : ces écarts sont des constats, **pas** une
  mesure de l'effet du rapport de l'explorateur local.
- Les appels API purement d'exploration sont 51 à 59 % des appels de l'implémenteur et 36 à 46 % de ceux du correcteur ; leurs tokens sont 33 à 51 % des tokens du rôle. [hypothèse, non testée] Une part importante de ces tokens est de la lecture de cache du contexte déjà accumulé à chaque appel ; la mesure ne le décompose pas par appel.

## Poids direct des appels purement d'exploration, par rôle, sous hypothèse

**Définition** [code]. Pour un bras et un rôle : le poids (pondéré) des tokens propres des appels API purement d'exploration, en part du poids du bras. C'est la part que l'on retirerait si, et seulement si, ces appels ne coûtaient rien et que rien d'autre ne changeait ; c'est l'hypothèse qui répond à la question « que pourrait au plus apporter une réduction de l'exploration », **en se limitant à ce poids direct**. La colonne « hors premier appel » retire le premier appel de chaque session, qui écrit dans le cache le prompt de la tâche. La dernière colonne donne le même poids direct en part des tokens du bras.

### v4

| Bras | Rôle | Part du rôle dans le poids du bras (5 min / 1 h) | Poids direct des appels purement d'exploration, en part du poids du bras (5 min / 1 h) | Idem hors premier appel de chaque session (5 min / 1 h) | Idem en part des tokens du bras |
| --- | --- | --- | --- | --- | --- |
| A | implementer | 36,4 % / 36,4 % | 16,6 % / 18,1 % | 12,5 % / 13,1 % | 19,3 % |
| A | corrector | 33,0 % / 33,4 % | 15,1 % / 16,2 % | 9,1 % / 9,0 % | 15,1 % |
| A | reviewer | 30,6 % / 30,2 % | 3,5 % / 3,5 % | 3,5 % / 3,5 % | 1,5 % |
| A | **trois rôles** | 100 % | 35,3 % / 37,9 % | 25,1 % / 25,7 % | 35,9 % |
| L | implementer | 27,5 % / 27,6 % | 14,3 % / 15,4 % | 10,4 % / 10,7 % | 16,4 % |
| L | corrector | 34,1 % / 34,1 % | 14,4 % / 15,7 % | 8,3 % / 8,5 % | 16,7 % |
| L | reviewer | 38,4 % / 38,3 % | 4,3 % / 4,6 % | 4,3 % / 4,6 % | 1,3 % |
| L | **trois rôles** | 100 % | 33,0 % / 35,7 % | 23,1 % / 23,8 % | 34,4 % |

### v5

| Bras | Rôle | Part du rôle dans le poids du bras (5 min / 1 h) | Poids direct des appels purement d'exploration, en part du poids du bras (5 min / 1 h) | Idem hors premier appel de chaque session (5 min / 1 h) | Idem en part des tokens du bras |
| --- | --- | --- | --- | --- | --- |
| A | implementer | 24,9 % / 24,8 % | 10,5 % / 11,6 % | 6,6 % / 6,7 % | 15,5 % |
| A | corrector | 23,2 % / 23,8 % | 11,5 % / 12,8 % | 5,4 % / 5,5 % | 12,6 % |
| A | reviewer | 51,8 % / 51,4 % | 7,9 % / 7,9 % | 7,9 % / 7,9 % | 4,6 % |
| A | **trois rôles** | 100 % | 29,9 % / 32,3 % | 19,9 % / 20,1 % | 32,7 % |
| L | implementer | 18,6 % / 18,9 % | 10,1 % / 11,2 % | 6,1 % / 6,3 % | 14,2 % |
| L | corrector | 26,9 % / 27,6 % | 11,8 % / 13,3 % | 4,7 % / 4,6 % | 12,4 % |
| L | reviewer | 54,5 % / 53,5 % | 9,4 % / 9,1 % | 9,4 % / 9,1 % | 7,2 % |
| L | **trois rôles** | 100 % | 31,3 % / 33,6 % | 20,1 % / 20,0 % | 33,8 % |

Lecture [code], avec Sonnet 5.5 à 0,10 : **v5, bras A** : implémenteur 11,6 % du poids du bras (6,7 % hors premier appel), correcteur 12,8 % (5,5 %), relecteur 7,9 % ; trois rôles 32,3 % (20,1 %). **Bras L** : 11,2 % (6,3 %), 13,3 % (4,6 %), 9,1 % ; 33,6 % (20,0 %). Écritures à 5 min : 29,9 % (19,9 %) pour A et 31,3 % (20,1 %) pour L. v4 : A 37,9 % (25,7 %), L 35,7 % (23,8 %). Avec Sonnet 5.5 à 0,20 : voir la section de sensibilité.

**Ce que ce chiffre borne, et ce qu'il ne borne pas.**
- Il borne le poids direct des tokens propres de ces appels, sous l'hypothèse qu'ils ne coûtent rien. **Il ne borne pas l'effet réel d'une réduction de l'exploration**, qui pourrait être plus grand : il ne compte pas le coût que le résultat d'une lecture impose ensuite dans le contexte relu en cache [inconnu] ; la classification est une borne basse (lectures cachées dans `bash_other`, appels mixtes classés en action), donc des appels de lecture ne sont pas dans ce poids. Il peut aussi être trop grand sur un point : une lecture qui précède une modification du même fichier compte comme exploration.
- Un appel de lecture produit l'information dont les appels suivants dépendent ; supprimer l'appel ne rendrait pas son poids à zéro net. Aucun gain n'est annoncé, ni de facture ni de poids : le plan est un abonnement, un poids de liste n'est pas une facture.
- Pour le relecteur, le poids direct vaut 7,9 à 9,4 % du poids du bras en v5 : il lit peu (un quart à un tiers de ses appels d'outil) et pèse 51 à 55 % du poids du bras. Ce sont deux constats ; la mesure ne les relie pas.
- Le poids du relecteur Opus en v5 est 1,9 à 2,9 fois celui de l'implémenteur ou du correcteur du même bras (écritures à 1 h ou à 5 min). C'est un autre poste que l'exploration ; la mesure n'établit pas qu'on puisse l'alléger, ni à quel coût de qualité.

## Accords et écarts

Avec les rapports versés [fichiers] : **aucun écart**. Jetons de facturation totaux et par modèle, par bras (A : référence ; L : bras), égaux pour v4 (A 4 134 697 ; L 2 870 126) et v5 (A 11 433 953 ; L 7 874 314) ; `paired_rule.premium_on_paired`
(A 9 178 695 ; L 7 874 314) et rapport 0,8579 retrouvés.

Avec `pat-19-exploration-results-v5.md` [fichiers] : **aucun écart** sur les décompositions non décisionnelles : coût par rôle sur D (A 3 457 293 / 3 209 177 / 2 512 225 ; L 2 200 468 / 2 972 315 / 2 701 531), différence de prime sur D
(1 304 381 = 1 256 825 + 236 862 − 189 306), relecteur Opus 27 % (A) et 34 % (L) de la prime sur D, lectures de cache 88 % et 86 %, sortie 2 %, tokens par modèle sur 12 tâches (A : Sonnet 8 050 520, Opus 3 383 433 ; L : 5 172 783 et
2 701 531). Avec le bilan : la répartition de la prime de A (39 %, 31 %, 30 %) est retrouvée. Le bilan dit que « l'exploration n'est pas isolée dans ces classes » et qu'une mesure sur les flux existants est possible : c'est ce que fait ce document.

Deux **précisions** (pas des écarts) : la définition de « premium » du rapport est la somme non pondérée des quatre classes ; les tokens de raisonnement n'y sont pas ajoutés (sous-ensemble de la sortie, vérifié par le fait que le coût de liste de l'hôte se
reproduit avec la sortie seule). Et le coût de liste de l'hôte (voir plus haut) diffère de la page de prix sur la seule lecture de cache de Sonnet.

## Ce qui reste inconnu

- [inconnu] La date de sortie des modèles 5.5, donc la validité exacte des prix avant le 2026-10-08 ; le prix est supposé inchangé depuis le 2026-10-05.
- [inconnu] Lequel du prix de la page (0,10) ou de celui que reproduit l'hôte (0,20) est le prix de lecture de cache de Sonnet 5.5 : les deux lectures sont données.
- [inconnu] Le coût que le résultat d'une lecture impose dans les appels suivants (contexte relu en cache) ; ce poids direct ne le compte pas.
- [inconnu] Si une exploration locale plus complète, ou un autre bras, changerait le nombre de lectures du cloud : un seul passage par couple tâche / bras, pas de répétition.
- [inconnu] Ce que valent ces nombres ailleurs que sur ces 12 tâches, vues plusieurs fois dans les campagnes antérieures.
- [inconnu] La séparation « lire pour comprendre » / « lire pour modifier » : la classification est mécanique, pas sémantique.

## Limites

- **12 tâches non indépendantes**, déjà jouées dans les campagnes antérieures ; v4 en compte 6 ; **un seul passage** par couple tâche / bras ; **aucune généralité** à tirer : ce sont deux campagnes, pas une preuve générale.
- **Prix de liste d'API utilisés comme poids sous un abonnement** : jamais une facture, jamais une économie. La pondération dépend de la grille (voir la sensibilité).
- **Scission de l'écriture de cache** : connue par les transcriptions (100 % à 1 h) mais absente des enregistrements versés ; les tableaux donnent les deux bornes, et la lecture « 5 min » est un contrefactuel.
- **Heuristiques de classification** : exploration comptée par lecture de la ligne de commande (bornes basses), sans interpréter ce que la commande fait réellement ; les appels `bash_other` incluent des lectures déguisées (script, redirection).
- **Pas de cause** : aucun de ces constats n'établit pourquoi un bras lit plus ou moins qu'un autre, ni qu'une réduction de lecture améliorerait ou dégraderait la qualité.
- **Relecteur et Opus** : la répartition par rôle recoupe le modèle (le relecteur est le seul Opus) ; elle ne les sépare pas.

## Pièces versionnées

- `plugins/foundry/tooling/foundry/cost_breakdown.py`, `plugins/foundry/tooling/foundry/pricing-breakdown-v1.json`, `plugins/foundry/tests/test_cost_breakdown.py`.
- `pat-19-cost-breakdown-v1-x4compare-1.json` et `pat-19-cost-breakdown-v1-x5compare-1.json` : agrégats seulement (comptes, tokens, USD de liste, base de validité de la grille, bloc `sensitivity`), sans chemin, commande, extrait, identifiant de session ni transcription.
  Reproduction : `python3 -m foundry.cost_breakdown --results <results> --report <report> --streams-dir <flux> --session-logs-dir <journaux> --override-rate claude-sonnet-5-5:cache_read=0.20 --out <fichier>` (les deux dernières entrées sont hors dépôt ; sans elles, seules les sections issues des fichiers versés sont produites).
- Contrôle avant versement : aucune occurrence du préfixe d'un dossier personnel, d'un nom d'utilisateur, d'un chemin de travail temporaire ou d'un secret dans les deux fichiers JSON ni dans ce document.

## Statut documentaire (R5)

Artefacts ajoutés ou modifiés, chacun avec son statut :

- **Nouveau module avec point d'entrée `python3 -m foundry.cost_breakdown`** (options `--results`, `--ledger`, `--report`, `--grid`, `--streams-dir`, `--session-logs-dir`, `--override-rate`, `--out`) : documenté ici (section « Méthode ») et dans une section ajoutée à
  [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md). Aucun verbe ni option de `foundry_cli.py` ni du lanceur `local_first_runner` n'a changé.
- **Nouvelle grille de prix `pricing-breakdown-v1.json`** (source, date de relevé, validité, base de la validité, schéma) : documentée ici (« Méthode »). `pricing-v1.json` et `cost_attribution.py` : inchangés, aucun consommateur touché.
- **Nouveaux documents** : ce document et les deux fichiers d'agrégats. **CHANGELOG** : une entrée.
- Constantes publiques / vocabulaires : les classes d'outils (`explore_read`, `explore_search`, `explore_bash`, `bash_other`, `edit`, `other`) et les ensembles de commandes de lecture sont définis ici ; aucune table de routage, clé de configuration ni protocole gelé n'a changé.
- Protocoles v1 à v5, configurations, résultats, rapports et pièces de preuve : inchangés ; aucun verdict recalculé ni requalifié.
- Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
