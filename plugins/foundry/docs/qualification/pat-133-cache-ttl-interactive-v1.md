# PAT-19 — Rejeu hors ligne du cache de prompt sur des sessions interactives réelles, dans les deux sens (PAT-133)

PAT-133. Cadre : FOUNDRY-ADR-0015 (lecteur hors ligne des journaux de session de l'hôte : seuls des compteurs de tokens, un alias de
modèle, un horodatage et un identifiant de session ; coût dérivé ; grille de prix versionnée), FOUNDRY-ADR-0008, FOUNDRY-ADR-0019 (une
configuration moins chère s'adopte sous observation, sans banc), AGENTS.md R5 et R6. Suite de
[`pat-19-cache-ttl-replay-v1.md`](pat-19-cache-ttl-replay-v1.md) (PAT-132), qui avait rejoué des sessions sans interface. **Aucune règle,
aucun protocole, aucun résultat ni verdict de PAT-132 n'est modifié ou recalculé (son JSON est inchangé octet pour octet). Aucun
réglage, profil d'agent ni routage n'est modifié.** Rien n'a été exécuté contre un modèle ni sur le réseau : l'outil lit des fichiers
locaux et n'en modifie aucun.

Légende. **[fichiers]** : recalculé sur les agrégats versés (`pat-133-cache-ttl-interactive-v1.json`). **[flux]** : lu dans les journaux de
session de l'hôte, qui restent hors dépôt et ne peuvent pas être revérifiés depuis le dépôt. **[doc]** : fait de la documentation
d'Anthropic **lu par le coordinateur le 2026-10-09** (`code.claude.com/docs/en/prompt-caching`, `env-vars`, `sub-agents`), **non revérifié
ici**. **[code]** : lu dans le dépôt.

## Résumé

- **Ce qui est mesuré** [flux][fichiers] : 3 sessions interactives réelles (conversation principale + journaux de sous-agents ; 2 568
  requêtes principales, 5 750 requêtes de sous-agents, 214 journaux de sous-agents), un instant de coupure fixe
  (`--until 2026-10-09T22:13:00Z`) parce que la plus récente était encore en cours d'écriture. **Les chiffres en dollars ne portent que
  sur l'une des trois sessions** (rang 2) : la grille de prix commence le 2026-10-05 et les deux autres sessions datent d'avant ; pour
  elles seuls les compteurs et les écarts sont disponibles. Prix de liste d'API comme poids sous un abonnement, jamais une facture.
- **Conversation principale, « 1 heure » vers « 5 minutes » (sens A) : perte nette sous les deux bornes** sur ce qui est tarifable (la
  lignée Opus 5.5 de la session de rang 2, 1 368 requêtes) : 297,04 USD réel, 1 110,10 simulé en borne prudente (+813,05, +273,7 %) et 652,20
  en borne favorable (+355,15, +119,6 %). Le risque est réel : sur l'ensemble des lignées principales, 21,3 % (prudente) / 10,5 % (favorable)
  des requêtes qui ont une précédente arrivent plus de 5 minutes après elle, et 262,9 M / 121,1 M de tokens de cache sont relus juste après
  de telles pauses (autant de réécritures au prix « 5 minutes » dans la simulation). Passer la conversation principale à « 5 minutes »
  **n'est pas justifié** par ces données.
- **Sous-agents, « 5 minutes » vers « 1 heure » (sens B) : gain net sous les deux bornes en agrégat, mais de signe opposé selon le
  modèle.** 4 207 requêtes tarifables : 328,37 USD réel, 316,14 (prudente, −12,22, −3,7 %) et 306,28 (favorable, −22,08, −6,7 %). **Sonnet 5.5 :
  gain net** (117,58 USD réel ; −33,83 / −38,95, soit −28,8 % / −33,1 %). **Opus 5.5 : perte nette** (210,79 USD réel ; +21,61 / +16,86, soit
  +10,2 % / +8,0 %). Seuls 4,0 % (prudente) / 2,1 % (favorable) des écarts de sous-agents dépassent 5 minutes, mais près de la moitié des
  tokens qu'ils écrivent le sont juste après une telle pause (30,6 M / 29,7 M sur 62,6 M), et la règle en reconnaît comme réécritures d'un préfixe
  déjà écrit 90 % (prudente) à 99 % (favorable) : c'est ce que la règle reconnaît, pas une mesure. Le seuil de rentabilité est le même pour les deux modèles (voir plus bas) : le « 1 heure » paye si au moins 38,5 % des tokens
  écrits à « 5 minutes » sont des réécritures dues à une expiration ; Sonnet est à environ 67–72 %, Opus à 24–26 %.
- **Rôle logique : non dérivable** dans les limites de FOUNDRY-ADR-0015 (le type d'agent n'est pas un champ permis) ; les figures sont par
  genre de lignée (principale / sous-agent) et par alias de modèle.
- **Un changement pourrait être justifié, pour les seuls sous-agents Sonnet, et à instruire par un ticket avec essai natif** (voir plus bas) ; rien
  n'est changé ici. **Ceci ne dit rien du quota d'abonnement : l'effet est inconnu.**
- **Ce qui ne se conclut pas** : trois sessions d'un même dépôt et d'un même utilisateur, une seule tarifable ; aucune généralité.

## Ensemble de sessions : critère écrit avant le calcul, et ce qu'il a sélectionné

**Critère (2026-10-10, avant tout chiffre, indépendant du résultat)** : les 5 conversations principales les plus récentes, par date de
modification du fichier, parmi les répertoires de projet de l'hôte dont le nom commence par celui du répertoire principal du dépôt
(ce qui inclut les arbres de travail du dépôt), qui ont **au moins un journal de sous-agent** (fichier `*.jsonl` sous
`<session>/subagents/`). Une session encore ouverte au moment de la sélection est rejouée figée à l'instant de la sélection
(`--until 2026-10-09T22:13:00Z`) : sans cela le rejeu ne serait pas reproductible. Les sessions sont nommées par rang (rang 1 = la
conversation principale qui commence le plus tôt), jamais par identifiant.

**Sélectionné** : le critère retient **3** répertoires de projet (le répertoire principal du dépôt et deux arbres de travail) ; **3**
conversations principales y ont des journaux de sous-agents (le plafond de 5 n'est pas atteint) ; toutes les trois sont rejouées.
**Non retenu, par le critère tel qu'il était écrit** : 6 conversations d'un autre répertoire de projet (nom différent, modifiées le 2026-10-03),
chacune avec un seul journal de sous-agent ; ce répertoire est peut-être celui d'un ancien emplacement du dépôt (non vérifié) ; elles n'ont
pas été rejouées et le critère n'a pas été modifié après coup.

| Rang | Premier jour d'activité (UTC) | Jours avec requêtes (UTC) | Alias principaux | Journaux de sous-agents |
|---|---|---|---|---|
| 1 | 2026-09-25 | 09-25, 09-26 | Opus 5.5 (+ un alias `<synthetic>` sans tokens) | 36 |
| 2 | 2026-10-04 | 10-04 à 10-09 | Sonnet 5.5, Opus 5.5 | 176 |
| 3 | 2026-10-04 | 10-04 | Opus 5.5 | 2 |

**Écart par rapport au critère « sessions de jours différents »** : les dates de modification des trois fichiers sont trois jours
différents, mais la date de modification n'est pas celle de l'activité ; par premier jour d'activité il n'y a que **deux** jours
distincts (les rangs 2 et 3 commencent le même jour). Les trois sessions sont bien trois conversations distinctes. [fichiers]

**Tarification** : la grille `pricing-breakdown-v1.json` commence au 2026-10-05 (`effective_from`) ; toute lignée qui a une requête avant
cette date, ou dont l'alias n'a pas d'entrée (`claude-sonnet-5`), est **`unavailable` en USD**, garde son alias et sort des deux côtés de toute
comparaison en dollars (ses compteurs restent dans la vue en tokens). Haiku 5.5 : prix dépendant d'une longueur de prompt que les journaux
ne portent pas, `unavailable`, jamais estimé. En conséquence, **le rang 1 et le rang 3 n'ont aucun chiffre en dollars** ; seule la session de
rang 2 en a (une partie de ses lignées). Ce n'est pas un choix : ajouter une entrée antérieure serait une hypothèse de prix non sourcée.

## Règle de simulation (écrite avant le calcul, avec une limite ; elle est aussi dans la docstring de `cache_ttl_replay.py`)

**Limite de « écrite avant le calcul »** : le coordinateur avait déjà fait, sur l'une des sessions rejouées (rang 2), une observation exploratoire non
publiée (nombre d'écarts de la conversation principale et des sous-agents au-dessus de 5 minutes, tokens écrits juste après, tokens encore lus).
Elle porte sur les quantités dont dépend le sens B ; la règle n'est donc pas aveugle à elle. Le recoupement plus bas n'est pas une validation
indépendante.

TTL = 300 s, TTL long = 3 600 s. Une requête est un `message.id` d'assistant. **Lignée** = les requêtes d'**un** fichier de journal avec **un**
alias de modèle, dans l'ordre du journal (un cache est par modèle). Un changement de modèle dans la conversation principale ouvre une
lignée par alias ; le retour au premier alias reprend la même lignée, et le temps passé ailleurs compte dans son écart. **Une compaction n'est
pas détectée** : la règle ne regarde que des compteurs et des écarts, une compaction apparaît comme une requête qui écrit un grand préfixe
et est tarifée comme n'importe quelle écriture ; on ne prétend pas la distinguer. Un journal de sous-agent est une lignée (une par alias s'il
en contient plusieurs). Le **genre** est la position du fichier : principale ou sous-agent.

**Écarts** : ceux de PAT-132, par lignée. Pour la requête `i`, la requête de référence `j` est la dernière requête antérieure de la lignée qui a
lu ou écrit du cache ; lecture haute `U_i = f_i − e_(j−1)` (le record qui précède la première requête pour `j = 0`) et lecture basse
`L_i = f_i − e_j` (`L_i ≤ U_i` ; la basse n'est pas un minorant rigoureux, comme en PAT-132). L'horodatage d'un enregistrement n'est pas le
début de la requête. La première requête d'une lignée n'a pas d'écart.

**Sens A, conversation principale (observé : ce que disent les journaux, ici « 1 heure » ; simulé : « 5 minutes »)** : exactement la règle
en session de PAT-132 sur chaque lignée principale. La requête `i` trouve le cache expiré quand son écart (haut pour la borne prudente, bas
pour la favorable) dépasse 300 s ; ce qu'elle a lu est alors réécrit au prix d'écriture « 5 minutes » ; toute écriture est au prix « 5 minutes » ;
le réel est la scission des journaux. Première requête d'une lignée qui lit du cache : `entry_expiry` avec, comme sessions antérieures, les
lignées principales des autres sessions rejouées qui commencent avant, **de même alias** ; aucune → expirée sous les deux bornes (qui a écrit
l'entrée n'est pas observable).

**Sens B, sous-agents (observé « 5 minutes » ; simulé « 1 heure »)** : l'expiration a réellement eu lieu dans ces journaux ; on simule la
réécriture qu'elle a causée. Coût simulé de la requête `i` (`I`, `O` entrée et sortie, `R`, `W` lus et écrits, `W5` écrits à « 5 minutes »,
`ρ_i` tokens reconnus comme réécriture d'expiration) : `I·entrée + (R + ρ_i)·lecture + (W − ρ_i)·écriture_1h + O·sortie` ; toute écriture passe
au tarif « 1 heure », `ρ_i` d'entre elles deviennent des lectures parce que l'entrée aurait été vivante. Reconnaissance **à partir des
compteurs et des écarts seulement** ; `P = R_j + W_j` est le nombre de tokens en cache après la requête de référence `j`, et `R_j` la part
que `j` avait elle-même lue (donc observée comme renvoyée à l'identique) :

- écart au-dessus de 3 600 s : expiré sous les deux durées, `ρ_i = 0` ; écart d'au plus 300 s : pas d'expiration, `ρ_i = 0` ; première requête
  d'une lignée (pas de `j`) : `ρ_i = 0` ;
**Hypothèse commune aux deux bornes : un contexte qui ne fait que grandir** (le préfixe en cache avant la pause est renvoyé à l'identique). Un déficit
de compteur après une pause peut aussi venir d'une compaction, d'une édition du contexte ou d'une invalidation sans rapport avec la durée de vie ; un
cache « 1 heure » aurait alors manqué lui aussi et du contenu nouveau est compté comme récupérable. **Aucune des deux bornes n'est donc un minorant du
gain** : la « prudente » ne l'est que relativement à la « favorable ».

- **borne prudente** (gain le plus faible des deux) : l'expiration n'est reconnue que si les deux lectures de l'écart la situent dans la fenêtre, `L_i > 300` et `U_i ≤ 3 600`, et
  `ρ_i = min(W5, max(0, R_j − R_i))` : seul compte ce que `j` avait lu ; ce que `j` a écrit lui-même n'est confirmé renvoyé à l'identique par
  aucune lecture avant la pause ;
- **borne favorable** (gain le plus fort des deux) : l'expiration est reconnue dès qu'une lecture la situe dans la fenêtre, `U_i > 300` et `L_i ≤ 3 600`, et
  `ρ_i = min(W5, max(0, P − R_i))` : tout le préfixe en cache avant la pause est supposé renvoyé à l'identique (contexte qui ne fait que
  grandir) et est la part de l'écriture que le réglage « 1 heure » aurait lue ;
- le déficit de compteur `P − R_i` (ou `R_j − R_i`) doit être positif : une écriture après une pause sans déficit n'est pas une réécriture
  d'expiration et reste une écriture « 1 heure » ; `ρ_i` est plafonné par `W5` : une écriture déjà à « 1 heure » dans le journal n'est pas
  comptée comme récupérable ; une requête dont les compteurs sont ambigus n'est jamais une référence (`ρ = 0`).

**Résultat (les deux sens)** : `simulé − réel` sous chaque borne ; **gain net** si les deux sont négatifs, **perte nette** si les deux sont
positifs, sinon **indécidable entre les bornes**. Même ensemble des deux côtés ; une lignée `unavailable` n'est d'aucun côté en USD.

**Inconnaissable avec les champs permis** [code] : quelle part d'une écriture après une pause est du contenu nouveau et quelle part est
l'ancien préfixe renvoyé (les formules la plafonnent par la taille du préfixe sous l'hypothèse d'un contexte qui ne fait que grandir, elles ne la mesurent pas) ; si un préfixe est partagé avec un autre
journal (en « 1 heure », une première requête de sous-agent aurait pu lire l'entrée écrite par un frère ; **non modélisé**, voir le volume
concerné plus bas) ; l'instant réel de début d'une requête ; plusieurs points d'ancrage de cache ; le fait documenté que le « 1 heure » est
ignoré tant que l'abonnement consomme des crédits d'usage [doc] ; le quota d'abonnement.

## Réparations du lecteur, 2026-10-10 : déclarées, faites après un premier passage qui refusait la plupart des journaux

Le lecteur de PAT-132 a été écrit pour des journaux sans interface. Un premier passage sur les journaux interactifs en a **refusé** la plupart
(`duplicate_differs`, `log_unreadable`) ; les sept réparations ci-dessous ont été faites **avant** tout chiffre de la règle sur la conversation
principale, mais **après** un premier chiffre partiel, que voici, gardé : premier passage, 428 requêtes de sous-agents seulement rejouées sur 5 750,
sens B prudente +1,31 USD (+13,2 %), favorable −0,08 USD (−0,8 %), indécidable ; **abandonné**, il ne portait que sur 7 % des requêtes. Aucune réparation ne change
une règle ci-dessus, aucune ne touche le mode de PAT-132 (les journaux de campagne gardent les contrôles stricts) :

1. les lignes sont séparées sur `\n` seulement (`str.splitlines` coupe aussi sur U+2028 et d'autres séparateurs, valides dans une chaîne
   JSON : plusieurs dizaines de lignes de la conversation principale étaient coupées en deux) ; ce changement vaut aussi pour le mode de PAT-132, sans
   effet sur des journaux qui n'en contiennent pas (ceux de PAT-132 n'en contenaient pas : aucun n'avait été refusé) ;
2. les enregistrements d'un même `message.id` peuvent différer par `output_tokens` seulement (le compte grandit pendant la diffusion ; 1 556
   enregistrements de ce type dans l'ensemble) : on garde le plus grand ; toute autre différence refuse le fichier (`duplicate_differs`) ;
3. une copie ultérieure d'un `message.id` dont les compteurs d'entrée, de lecture et d'écriture sont tous nuls est ignorée (1 cas) ; le
   premier enregistrement nul, s'il vient d'abord, est remplacé par le vrai ;
4. une requête dont les deux classes d'écriture ne totalisent pas son total est marquée `ambiguous` (conservée dans la suite pour les écarts,
   jamais référence, ses tokens d'aucun côté) au lieu de faire refuser le journal ; 0 requête dans l'ensemble après la réparation 3 ;
5. un dernier enregistrement illisible est ignoré avec `--until` seulement (il est postérieur à l'instant par construction) ;
6. la **vue en tokens** (compteurs et écarts) est calculée sans prix, pour toutes les lignées lisibles, ce qui a été décidé quand il est apparu
   que la grille ne couvrait qu'une session sur trois ; la vue en USD ne garde que les lignées entièrement tarifables ;
7. un alias exclu garde son nom dans le nouveau mode (`lineages_unavailable_in_usd_or_unreadable`) ; **le mode de PAT-132 ne le fait pas**
   (ses sessions exclues portent seulement session, rôle et raison) : non modifié pour garder ses sorties identiques ; aucune session de
   PAT-132 n'a été exclue.

## Corrections du 2026-10-10, après la revue indépendante (aucune règle changée)

Faites après les premiers chiffres du document, déclarées ; les valeurs d'avant sont gardées ici.

- **`requests_found_expired` (sens A)** comptait aussi les premières requêtes de lignée (sans précédente) ; renommé en deux clés :
  `requests_found_expired` (avec précédente, cohérent avec les écarts) et `first_requests_found_expired`. Valeurs d'avant (prudente / favorable) : toutes
  lignées principales 551 / 274 (maintenant 545 / 268 + 6 premières requêtes) ; Opus 444 / 222 (441 / 219) ; Sonnet 105 / 50 (104 / 49) ; `<synthetic>` 2 / 2 (0 + 2
  premières). Aucun coût, aucun verdict ni aucun autre chiffre ne change.
- **`--until` et `subagent_logs_found`** : seuls comptent les journaux de sous-agent ayant un enregistrement à l'instant ou avant ; un journal sans requête
  mais avec un enregistrement est listé (`no_request`) au lieu de disparaître. Effet sur les chiffres versés : **aucun compte ne change** (36, 176, 2) ; la liste
  `lineages_unavailable_in_usd_or_unreadable` passe de 59 à 60 entrées (un journal sans requête, désormais listé).
- Ajouts sans changement de chiffre existant : `main_returns_to_an_earlier_alias`, `tokens`/`tokens_by_bound`/`simulated_usd` par lignée,
  `delta_usd_if_no_recognised_rewrite_is_real`, `recognised_share_of_5m_writes`, `wrong_share_of_recognised_rewrites_that_cancels_the_delta`,
  `cold_start_write_tokens_not_modelled`, `cold_start_envelope_usd_not_modelled`. `first_day_utc` est maintenant converti en UTC (aucune valeur ne change).
- Reformulations : hypothèse commune d'un contexte qui ne fait que grandir (plus de « certaine » ni de minorant), limite de « écrite avant le calcul »,
  « reconnus comme », ADR-0019 par analogie, ρ/W sur deux périmètres.

## Résultats

Tous les chiffres : [fichiers][flux]. Prix de liste, poids et non facture. « Prudente » et « favorable » : voir la règle.

### Sens A : conversation principale, « 1 heure » (observé) vers « 5 minutes » (simulé)

| Lignées principales | Requêtes (avec précédente) | Écarts > 300 s, prudente / favorable | dont > 3 600 s | Tokens lus juste après, prudente / favorable | Tokens écrits juste après |
|---|---|---|---|---|---|
| Toutes (alias Opus 5.5, Sonnet 5.5, `<synthetic>`) | 2 568 (2 562) | 545 (21,3 %) / 268 (10,5 %) | 74 / 37 | 262,9 M / 121,1 M | 17,3 M / 16,7 M |
| Opus 5.5 | 2 018 (2 015) | 441 (21,9 %) / 219 (10,9 %) | 68 / 34 | 212,2 M / 98,0 M | 15,7 M / 15,3 M |
| Sonnet 5.5 | 548 (547) | 104 (19,0 %) / 49 (9,0 %) | 6 / 3 | 50,6 M / 23,1 M | 1,5 M / 1,4 M |

Écritures observées : 23 151 554 tokens à « 1 heure », 0 à « 5 minutes ». Tokens de cache relus et réécrits au prix « 5 minutes » dans la
simulation : 263,0 M (prudente) / 121,3 M (favorable). Premières requêtes de lignée qui lisent du cache : 6 requêtes, 158 964 tokens
(Opus 3 requêtes, Sonnet 1, `<synthetic>` sans tokens).

| Résultat en USD (tarifable : Opus 5.5, session de rang 2) | Réel | Simulé prudente | Δ prudente | Simulé favorable | Δ favorable | Résultat |
|---|---|---|---|---|---|---|
| 1 368 requêtes | 297,04 | 1 110,10 | +813,05 (+273,7 %) | 652,20 | +355,15 (+119,6 %) | **perte nette** |

Sonnet 5.5 principal (548 requêtes) : `unavailable` en USD (au moins une requête avant la grille) ; rangs 1 et 3 : `unavailable`
(avant la grille), leurs écarts sont dans le tableau ci-dessus. Écart maximal observé 250 680 s (environ 2,9 jours).

### Sens B : sous-agents, « 5 minutes » (observé) vers « 1 heure » (simulé)

| Lignées de sous-agents | Requêtes (avec précédente) | Écarts > 300 s, prudente / favorable | de 300 à 3 600 s | > 3 600 s | Tokens écrits juste après | Tokens lus juste après | Tokens reconnus ρ | Requêtes avec réécriture reconnue |
|---|---|---|---|---|---|---|---|---|
| Toutes | 5 750 (5 536) | 224 (4,0 %) / 119 (2,1 %) | 218 / 116 | 6 / 3 | 30,59 M / 29,73 M | 27,84 M / 0,92 M | 27,53 M / 29,47 M | 104 / 114 |
| Opus 5.5 | 2 844 (2 697) | 69 (2,6 %) / 36 (1,3 %) | 67 / 35 | 2 / 1 | 8,84 M / 8,12 M | 8,16 M / 0,65 M | 7,67 M / 8,28 M | 31 / 34 |
| Sonnet 5.5 | 2 376 (2 322) | 153 (6,6 %) / 82 (3,5 %) | 149 / 80 | 4 / 2 | 21,70 M / 21,56 M | 19,63 M / 0,27 M | 19,83 M / 21,15 M | 72 / 79 |
| Sonnet 5 (alias sans prix) | 522 (512) | 2 / 1 | 2 / 1 | 0 | 0,05 M / 0,05 M | 0,05 M / 0 | 0,03 M / 0,04 M | 1 / 1 |
| Haiku 5.5, `<synthetic>` | 6 (5), 2 (0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Écritures observées : 62 634 449 tokens à « 5 minutes », 0 à « 1 heure ». Premières requêtes de lignée (écritures à froid, **non modélisées**,
peut-être partagées avec un frère en « 1 heure » : inconnu) : 214 requêtes, 2 957 304 tokens écrits (4,7 % des écritures), 98 725 lus.

| Résultat en USD | Requêtes tarifables | Réel | Simulé prudente | Δ prudente | Simulé favorable | Δ favorable | Résultat |
|---|---|---|---|---|---|---|---|
| Tous sous-agents tarifables (session de rang 2) | 4 207 | 328,37 | 316,14 | −12,22 (−3,7 %) | 306,28 | −22,08 (−6,7 %) | **gain net** |
| dont Sonnet 5.5 | 2 256 | 117,58 | 83,75 | −33,83 (−28,8 %) | 78,63 | −38,95 (−33,1 %) | **gain net** |
| dont Opus 5.5 | 1 951 | 210,79 | 232,40 | +21,61 (+10,2 %) | 227,66 | +16,86 (+8,0 %) | **perte nette** |

Les rangs 1 et 3 (sous-agents : 1 096 et 21 requêtes) n'ont pas de chiffre en USD ; leurs écarts : rang 1, 2 écarts > 300 s (prudente) / 1
(favorable) ; rang 3, aucun. Les lignées de sous-agents `unavailable` en USD : 37 Opus 5.5 et 5 Sonnet 5.5 (avant le 2026-10-05), 10
Sonnet 5 (alias sans entrée de prix), 2 `<synthetic>`, 1 Haiku 5.5 (prix selon la longueur du prompt).

**Seuil de rentabilité du « 1 heure »** [code] : pour des tokens écrits à « 5 minutes » `W` dont `ρ` sont des réécritures d'expiration, le
coût change de `W·(écriture_1h − écriture_5m) − ρ·(écriture_1h − lecture)`, soit `ρ/W` ≥ (2 − 1,25) / (2 − 0,05) = 5/13 ≈ 38,5 % de l'entrée pour
gagner (ce rapport est le même pour Sonnet et Opus, dont les tarifs sont des multiples de l'entrée). Rapports observés, deux périmètres à ne pas
confondre. **Vue en tokens, toutes lignées lisibles de l'alias** (tarifables ou non) : Sonnet 5.5 ≈ 67 % (prudente) à 72 % (favorable) ; Opus 5.5 ≈ 24 %
à 26 %. **Sous-ensemble tarifé, celui des verdicts** : Sonnet 5.5 68,4 % / 72,9 % ; Opus 5.5 28,3 % / 30,5 % ; d'où les deux signes de résultat. [fichiers]

**Robustesse (recalculée)** : si une part `f` des réécritures reconnues était fausse (compaction, édition, invalidation sans rapport avec la durée),
le delta serait `A − (1 − f)·B` avec `A` le delta si aucune réécriture reconnue n'est réelle (Sonnet : +43,53 USD, toutes écritures à « 1 heure »
sans rien de sauvé) et `B` la valeur des réécritures reconnues. Le verdict de Sonnet 5.5 cesse d'être un gain si **43,7 % (borne prudente) ou 47,2 %
(borne favorable)** des réécritures reconnues sont fausses (sous-ensemble tarifé ; en agrégat des sous-agents tarifables : 8,9 % / 15,0 %).
Pour Opus 5.5 (perte nette) la question ne se pose pas dans ce sens : même si toutes les réécritures reconnues étaient réelles, le delta resterait
positif. Cette robustesse ne dit rien de la part réelle de réécritures fausses, qui est **inconnue**. [fichiers]

### Par session (rangs)

| Rang | Genre | Requêtes | Écarts > 300 s (prudente / favorable) | Tokens écrits juste après (prudente / favorable) | Résultat en USD |
|---|---|---|---|---|---|
| 1 | principale | 583 | 65 / 31 | 2,75 M / 2,67 M | `unavailable` (avant la grille) |
| 1 | sous-agents | 1 096 | 2 / 1 | 0,05 M / 0,05 M | `unavailable` |
| 2 | principale | 1 916 | 472 / 234 | 14,47 M / 14,00 M | Opus : perte nette (ci-dessus) ; Sonnet `unavailable` |
| 2 | sous-agents | 4 633 | 222 / 118 | 30,54 M / 29,68 M | gain net (Sonnet gain, Opus perte) |
| 3 | principale | 69 | 8 / 3 | 0,06 M / 0,03 M | `unavailable` |
| 3 | sous-agents | 21 | 0 / 0 | 0 | `unavailable` |

Pour mémoire, ces chiffres recoupent à l'arrondi l'observation exploratoire non publiée du coordinateur sur la même session (232 écarts
principaux sur 1 912 au-dessus de 5 minutes ; 118 pauses de sous-agents ; environ 29,7 M tokens écrits juste après, moins de 1 M encore lus) : le
lecteur les retrouve (234 sur 1 914 ; 118 ; 29,68 M ; 0,92 M sous la lecture basse).

## Conformité à FOUNDRY-ADR-0015 et à la vie privée

- **Champs lus** : compteurs de tokens, alias de modèle (`message.model`), horodatage, identité de session (le fichier lui-même, jamais émise). **Champs
  lus en plus de la liste de l'ADR, nommés** : `type` (choisir les enregistrements d'assistant, et l'horodatage de l'enregistrement qui précède
  le premier) et `message.id` (dédoublonnage en mémoire des enregistrements de diffusion). Rien d'autre : ni invite, ni réponse, ni chemin, ni
  nom de fichier, ni commande, ni nom d'outil, ni type d'agent, ni extrait. Le champ `fields_read_beyond_adr_0015` du JSON les liste.
- **Provenance** (vocabulaire de l'ADR) : compteurs, alias et horodatages `host_reported` ; tout montant en dollars, réel ou simulé,
  `pricing_derived` ; toute lignée sans prix utilisable `unavailable` (elle garde son alias) ; `client_observed` : aucun (le lecteur n'observe rien
  par lui-même).
- **Pas de réseau, aucun journal modifié, idempotent** : testés (`test_cache_ttl_interactive.py`). Les sessions sont nommées par rang
  (`session-02/subagent-17`) ; aucun identifiant, nom de fichier, de répertoire de projet, chemin ni nom d'utilisateur n'est dans le JSON ni
  ici (contrôle à l'exécution dans le test).
- Une lignée dont un journal est illisible ou ambigu est listée avec un code fixe et l'alias `unavailable` (l'alias n'est pas connu quand le
  fichier n'a pas pu être lu) ; aucune lignée de l'ensemble versé n'est dans ce cas.

## Ce qui reste inconnu

- Quelle part d'une écriture après une pause est du contenu nouveau : les deux bornes l'encadrent (voir la règle) sans la mesurer. L'écart entre
  elles est de 9,9 USD sur 328 pour les sous-agents (−12,22 contre −22,08) et large sur la conversation principale (545 pauses de plus de
  5 minutes en lecture haute contre 268 en lecture basse) : la lecture haute compte la durée de la réponse précédente.
- Le partage de préfixe entre journaux de sous-agents en « 1 heure » : non modélisé, d'effet favorable au « 1 heure » ; les bornes ne le contiennent
  pas. Ordre de grandeur, premières requêtes de lignées de sous-agents : toutes lignées lisibles, 214 requêtes, 2 957 304 tokens écrits (4,7 % des
  écritures), dont **Opus 5.5 : 2 031 264 tokens écrits et 34 029 lus** (147 requêtes) ; seuls 98 725 tokens sont lus aujourd'hui sur ces premières
  requêtes, tous alias confondus. Sur le **sous-ensemble tarifé**, qui est celui du verdict : Opus 5.5 1 546 265 tokens, au plus **12,06 USD** si chacun
  devenait une lecture « 1 heure » (borne haute : 1 546 265 × (8 − 0,2) / 10⁶), contre un delta de +16,86 USD (favorable) ou +21,61 (prudente) ;
  Sonnet 5.5 676 184 tokens, au plus 2,64 USD ; tous sous-agents tarifés 2 222 449 tokens, au plus 14,70 USD. Sur Opus la perte resterait donc
  positive (au moins +4,8 USD en favorable) même si tout ce partage était réel, mais l'ordre de grandeur n'est pas négligeable.
- Les sessions de rang 1 et 3 en dollars, et la lignée Sonnet principale du rang 2 : `unavailable`, pas de prix avant le 2026-10-05.
- Le comportement du « 1 heure » pendant que l'abonnement consomme des crédits d'usage (ignoré selon la documentation) [doc], l'effet sur le
  **quota** d'abonnement, la qualité et la durée sous un autre réglage : **inconnus**, rien n'est conclu.
- La structure réelle des points d'ancrage de cache de l'hôte ; l'instant exact de début de chaque requête ; la part des pauses due à une
  attente humaine ou à des tests longs ; le rôle logique de chaque sous-agent.

## Quel changement serait justifié, et par quel moyen (rien n'est fait ici)

Selon la documentation [doc], un abonnement donne par défaut « 1 heure » à la conversation principale et « 5 minutes » aux sous-agents ;
`promptCacheTtl` règle la conversation principale, `subagentPromptCacheTtl` les sous-agents et les requêtes hors conversation principale
(variables d'environnement équivalentes, Claude Code 2.1.242 et plus), et un profil d'agent peut fixer `experimental: cacheTtl: 5m|1h`
(2.1.248 et plus). FOUNDRY-ADR-0019 (cité **par analogie** : elle régit la promotion d'un modèle sur un rôle, pas un réglage de cache) : une configuration moins chère s'adopte sous observation, sans banc.

- **Conversation principale** : **rien à changer**. Sur ce qui est tarifable, passer à « 5 minutes » est une perte nette sous les deux bornes
  (+120 % à +274 % du coût de liste) ; garder `promptCacheTtl` à « 1 heure ». Le moyen d'un tel changement serait un réglage de l'utilisateur
  (`promptCacheTtl`) : ce n'est pas proposé.
- **Sous-agents** : le résultat est de **signe opposé selon le modèle** sur ces données (Sonnet gain net, Opus perte nette). Un réglage global
  `subagentPromptCacheTtl: 1h` s'applique à tous les sous-agents, Opus compris, et rendrait ce mélange ; le champ `experimental.cacheTtl: 1h`
  de l'**en-tête d'un profil d'agent** cible un profil donc un modèle. Un changement justifié, s'il est décidé, serait donc **par profil, pour
  les profils sur Sonnet**, à instruire par un ticket qui décide aussi de la mesure sur observation (par analogie avec ADR-0019) et de l'effet sur le quota ;
  `--settings` du lanceur ne concerne que les sessions sans interface (hors de ce rejeu, voir PAT-132 pour la question de transmission et la
  liste fermée de variables d'AGENTS.md R6). Aucun profil, réglage ni routage n'est modifié ici.
- **Sessions sans interface** : inchangé par ce rapport (résultat de PAT-132, distinct).
- Ces énoncés valent pour **trois sessions, dont une tarifable** ; ils ne se généralisent pas, et ne disent **rien du quota d'abonnement**.

## Limites

- **Contexte qui ne fait que grandir** : hypothèse des deux bornes du sens B ; ni l'une ni l'autre n'est un minorant du gain (voir la règle).
- **Retours à un alias après un changement de modèle** (sens A) : la référence haute d'une requête est la requête précédente du **même alias**, conformément
  à la règle écrite ; elle reste un majorant mais gonfle la borne prudente d'au plus une requête par retour. Nombre de tels retours dans la
  conversation principale : 3 (rang 1), 4 (rang 2), 0 (rang 3), 7 au total. [fichiers]
- Trois sessions d'un seul dépôt et d'un seul utilisateur ; chiffres en dollars sur une session ; la plus récente est figée à un instant.
- Prix de liste d'API utilisés comme poids sous un abonnement : jamais une facture, jamais une économie ; rien sur le quota.
- Simulation à une seule lignée par fichier et par alias, deux lectures de l'écart dont aucune n'est exacte, aucun point d'ancrage multiple,
  aucun cache partagé entre journaux (voir « Inconnu »). Une compaction n'est pas détectée. Les sous-agents imbriqués sont comptés comme des
  sous-agents (position sous `subagents/`), sans distinction de profondeur.
- Un enregistrement `isSidechain` éventuellement présent dans le journal principal n'est pas distingué (le champ est hors de la liste de
  l'ADR) : il serait fusionné dans la lignée de son alias.

## Pièces versionnées

- `plugins/foundry/tooling/foundry/cache_ttl_replay.py` (nouveau mode `--host-session`, option `--until`),
  `plugins/foundry/tests/test_cache_ttl_interactive.py`, grille inchangée `pricing-breakdown-v1.json`.
- `pat-133-cache-ttl-interactive-v1.json` : agrégats seulement (par session nommée par rang, par genre, par genre et alias, par lignée nommée par
  rang, lignées `unavailable`), sans chemin, invite, commande, extrait, nom de fichier ni identifiant de session. Reproduction :
  `python3 -m foundry.cache_ttl_replay --host-session <journal principal> [--host-session ...] --until 2026-10-09T22:13:00Z --out <fichier>`
  (les entrées sont hors dépôt ; la session de rang 2 était encore ouverte, d'où `--until`).
- `pat-19-cache-ttl-replay-v1.json` et `pat-19-cache-ttl-replay-v1.md` (PAT-132) : inchangés.

## Statut documentaire (R5)

- **Nouveau mode de `python3 -m foundry.cache_ttl_replay`** : option `--host-session MAIN_LOG` (répétable ; les journaux de sous-agents sont les
  `*.jsonl` sous `<session>/subagents/`, imbriqués compris ; remplace `--ledger` et `--session-logs-dir`, qui restent obligatoires sans lui) et
  option `--until ISO_INSTANT` (n'existe qu'avec `--host-session` ; ignore tout enregistrement postérieur) ; sortie `foundry.cache-ttl-replay.interactive.v1` ;
  codes de sortie 0 ou 2 inchangés ; les messages d'erreur du mode de PAT-132 sont inchangés. Documenté ici et dans la section ajoutée à
  [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md). Aucun verbe ni option de `foundry_cli.py` ni du lanceur `local_first_runner` n'a changé.
- **Constantes, fonctions et vocabulaire publics** : `LONG_TTL_SECONDS` (3 600), `KINDS`, `DIRECTION`, `PROVENANCE`, `FIELDS_BEYOND_ADR_0015`,
  `discover_subagent_logs`, `split_by_alias`, `recovered_reads`, `request_cost_long`, `lineage_rows`, `summarise`, `analyse_interactive`, le
  paramètre `interactive` de `read_host_requests` ; les clés `by_kind`, `by_kind_and_model`, `lineages`,
  `lineages_unavailable_in_usd_or_unreadable`, `simulated_direction` (`1h_to_5m`, `5m_to_1h`), `tokens_by_bound`,
  `recovered_read_tokens`, `requests_with_recognised_expiry_rewrite`, `first_requests_found_expired`, `main_returns_to_an_earlier_alias`,
  `delta_usd_if_no_recognised_rewrite_is_real`, `recognised_share_of_5m_writes`, `wrong_share_of_recognised_rewrites_that_cancels_the_delta`,
  `cold_start_write_tokens_not_modelled`, `cold_start_envelope_usd_not_modelled`, le code `no_request` d'un journal de sous-agent sans requête, `requests_ambiguous_counters_on_neither_side`, `usd`, `first_day_utc`,
  `days_utc_with_requests`, `distinct_first_days_utc` ; les codes `no_price_for_alias_on_day` et `price_depends_on_prompt_length` : définis ici.
  Les codes d'exclusion et le vocabulaire de résultat de PAT-132 sont repris tels quels. Aucune table de routage, clé de configuration,
  réglage du lanceur ni protocole gelé n'a changé.
- **Nouveaux documents** : ce document et le fichier d'agrégats. **CHANGELOG** : une entrée.
- Le détecteur de FOUNDRY-123 n'est pas livré : ce statut est affirmé ici et vérifié en revue, non appliqué mécaniquement.
