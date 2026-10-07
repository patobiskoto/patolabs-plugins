# PAT-19 — Résultats du tamis d'exploration local, version 2

PAT-115. Cadre : PAT-ADR-0015 (exploration jugée seulement par son effet aval, tamis local sans cloud à règle écrite
d'avance, trois verdicts séparés, aucune promotion, une donnée absente n'est jamais zéro hors de ce filtre),
FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, arrêt séquentiel). Protocole gelé :
[`pat-19-protocol-v2.md`](pat-19-protocol-v2.md) (section 5) ; configuration : `pat-19-campaign-v2.json` ; vérité terrain :
`pat-19-exploration-truth-v2.json` ; lanceur : [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md) (section « Protocole v2 ») ;
modèle de ce document : [`pat-19-screening-results-v1.md`](pat-19-screening-results-v1.md). Aucune règle ni coordonnée du
protocole v2 n'est modifiée par ce document ; il consigne un essai réel et s'arrête là.

## Résumé

Campagne `pat-19-xscreen-1`, tamis d'exploration seul (enveloppe sans cloud), exécutée le 2026-10-07 de 01:39 à 05:14
(heure locale). **Aucun candidat n'atteint le seuil** : le meilleur rappel moyen de fonctions est **0,25** (qwen3.6),
sous le seuil de 0,5, et **aucun candidat n'a une précision moyenne de fichiers >= 0,5** (meilleure : 0,333). Par la règle
gelée de la section 5, la campagne s'arrête sur « conserver le cloud » pour cet usage (exploration locale en lecture
seule). Le rapport mécanique est complet et conclut `no_candidate_meets_precision_floor: keep_cloud`,
`campaign_conclusion` `keep_cloud`. La comparaison (bras A / L / E) n'a pas été lancée ; aucun quota cloud n'a été
dépensé (`cloud_executions` = 0 sur les 30 résultats, enveloppe sans cloud).

Observation, pas un résultat sur le rôle : sur 30 explorations, **24 n'ont pas rendu de rapport exploitable dans les bornes**
(15 coupées par le temps, 8 par les étapes, 1 rapport invalide) ; sur les 4 qui ont rendu un rapport non vide, les 4 ont nommé le bon
fichier. La limite observée est de conclure en 10 min et 25 étapes, pas la précision de localisation quand un rapport
existe. Par PAT-ADR-0015, une exploration ne se juge que par son effet aval, qui n'a pas été mesuré.

## Conditions de l'essai

- Code : `main` à `3af0932` (PAT-114 fusionné), arbre de travail gelé ; harnais omp 18.6.1 ; LM Studio 0.4.25+1 ;
  contexte >= 65 536. Empreinte de campagne `6830629c…` : égale au sha256 de `pat-19-campaign-v2.json` à `3af0932`
  (vérifié par le coordinateur, recalculé ici sur le fichier versé). Manifeste `8ac65091…`, enveloppe `3c5a78f6…`.
- Enveloppe : `envelope-pat-19-xscreen-1.json` (mode `screen_exploration`, 0 exécution cloud, 0 token premium, 36 000 s).
- Explorateur : `omp --tools=read,grep,glob`, bundle en lecture seule, bornes 10 min et 25 étapes ; un candidat à la fois,
  chargé avec sa `load_command` épinglée.
- Machine dédiée (condition de la section 8, contrôle refusant) : l'application ChatGPT (environ 3 Gio) a été quittée avant
  l'essai avec l'accord du mainteneur ; les services d'un autre projet avaient été arrêtés la veille. 32 préflights au
  registre, 30 verts et **2 refusés**, tous deux pendant le dernier candidat (voir plus bas) ; 7 sessions du lanceur
  (`l01` à `l07`). Temps total des tentatives : 12 729,5 s (somme des `wall_seconds`).
- Moteurs : Muse Glimmer est un GGUF servi par llama.cpp, les quatre autres candidats par MLX (variable de plus dans la
  comparaison entre candidats, comme en v1).

## Résultats par candidat

Recalculés à partir des `results-pat-19-xscreen-1.jsonl` versionnés (30 enregistrements `attempt`) et rapprochés de
`report-pat-19-xscreen-1.json` (mêmes valeurs). Les chiffres de rappel et de précision sont les moyennes sur les 6 tâches,
un refus compté 0.

| Candidat | Rappel fonctions | Précision fichiers | Rappel fichiers | Secondes | Issues des 6 tentatives | Tokens d'entrée (flux) |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3.8-27b-mlx-6bit | 0 | 0 | 0 | 3600,1 | 6 bornes de temps | 1,49 M |
| qwen3.8-27b-mlx-4bit | 0,167 | 0,167 | 0,167 | 3589,7 | 5 bornes de temps, 1 rapport noté | 1,05 M |
| qwen3.6-35b-a3b-mlx-4bit | 0,25 | 0,333 | 0,333 | 891,6 | 3 bornes d'étapes, 1 rapport invalide (clés manquantes), 2 rapports notés | 3,60 M |
| muse-glimmer-30b-gguf | 0 | 0 | 0 | 3437,1 | 4 bornes de temps, 2 bornes d'étapes | 3,28 M |
| qwen3-coder-30b-a3b-mlx-4bit | 0,167 | 0,167 | 0,167 | 1211,0 | 3 bornes d'étapes, 2 rapports notés mais VIDES, 1 rapport noté | 2,00 M |

Totaux : 15 bornes de temps, 8 bornes d'étapes, 1 rapport invalide, 6 tentatives notées (4 rapports non vides, 2 vides) =
30. Pour omp, la borne d'étapes est appliquée par le lanceur en tuant le groupe de processus : la coupure est enregistrée à
26 étapes pour une borne de 25 (même décalage d'un qu'en v1, sans effet sur l'issue). Les débits de génération et de
préremplissage ne sont pas exposés par le flux : inconnus. Les tokens d'entrée du flux (1,0 M à 3,6 M par candidat)
montrent à nouveau la croissance du contexte ; son rôle dans les coupures est une hypothèse, non vérifiée.

Les secondes du tamis sont la somme des `wall_seconds` des tentatives ; elles départageraient une égalité, ce qui n'a pas
eu lieu.

### Les quatre explorations qui ont rendu un rapport non vide

| Candidat | PR | Rappel fonctions | Précision fichiers | Rappel fichiers |
| --- | --- | --- | --- | --- |
| qwen3.8-27b-mlx-4bit | 24 | 1,0 | 1,0 | 1,0 |
| qwen3.6-35b-a3b-mlx-4bit | 30 | 1,0 | 1,0 | 1,0 |
| qwen3.6-35b-a3b-mlx-4bit | 19 | 0,5 | 1,0 | 1,0 |
| qwen3-coder-30b-a3b-mlx-4bit | 48 | 1,0 | 1,0 | 1,0 |

Chaque tâche n'a qu'un fichier produit (section 4 du protocole) et cinq des six tâches du tamis partagent `linear.py` :
le rappel de fichiers est presque trivial, c'est pourquoi le rappel de fonctions décide. 4 rapports sur 30 tentatives ne
sont **pas** un taux de réussite : l'échantillon est trop petit et ces 4 tentatives ne sont pas tirées au hasard (elles
sont celles qui ont conclu à temps).

### Rapports vides de qwen3-coder

Deux tentatives de qwen3-coder (PR 27 et PR 19) sont notées (`SCORED`, rappel 0) alors que le modèle a répondu en 5 à 6,5 s,
sans aucune étape d'outil, par un rapport aux listes vides (`{"files": [], "functions": [], ...}`), sans avoir lu l'énoncé.
Constat lu dans les flux bruts hors dépôt. Ces deux tentatives sont les **premières de leur lancement**, juste après un
rechargement du modèle (lancements `l06` et `l07`) ; si le rechargement a influencé ces réponses, c'est inconnu. Elles sont
décidées et jamais rejouées sous la règle gelée.

## Dernier candidat et mémoire de la machine

Après sa deuxième tâche (fin de `l05`), le préflight de machine dédiée a refusé : `dedicated_machine_free_memory_below_minimum:9<35`
(9 % de mémoire libre) ; il a refusé de nouveau après trois tâches de plus (`l06`, 17 < 35). Aucun processus étranger n'était en
cause (les plus gros hors LM Studio : OrbStack, environ 0,9 à 1,1 Gio, Arc, environ 0,3 Gio, sous le seuil de 2 Gio) : c'est la
croissance mémoire du modèle lui-même ; la mémoire libre est revenue à 87 % une fois le modèle déchargé. Conformément à la
section 11 du protocole, le coordinateur a libéré la machine (déchargement puis rechargement avec la commande épinglée) et a
repris sous le même identifiant de campagne (lancements `l05`, `l06`, `l07`). Aucune règle n'a été modifiée.

Cas hypothétique, **non un résultat** : si les deux rapports vides avaient été parfaits (rappel 1,0 chacun), ce candidat aurait
atteint exactement 0,5 de rappel moyen de fonctions et 0,5 de précision moyenne de fichiers ((1+1+1)/6), seuil inclus. Ce calcul
ne change ni le verdict ni les enregistrements.

## Règles effectivement exercées

Les valeurs validées par le mainteneur le 2026-10-06 et les règles fixées par le coordinateur (2026-10-07, avant tout essai du
corpus) sont celles de l'en-tête de `pat-19-protocol-v2.md`. Parmi elles, ont été exercées :

- **« Exploration coupée par une borne = refus »** (annoncée au mainteneur comme correction d'exécution) : 23 tentatives
  (15 + 8). Sans elle, ces 23 tentatives auraient pu être notées sur un éventuel brouillon, dont le contenu est inconnu ; l'effet
  d'une autre règle de notation sur la conclusion est donc non établi.
- **« Un refus compte 0 »** (règle du coordinateur, non soumise au mainteneur) : 24 tentatives refusées (23 coupures + 1 rapport
  invalide) comptées 0 dans les moyennes. Écart assumé avec « une donnée absente n'est jamais zéro », limité à ce filtre.
- **« Durée >= borne = refus »** : les 15 coupures par le temps sont à 600,0 s ; la seule tentative de qwen3.8 4 bits notée est à
  589,6 s, donc sous la borne.
- **Liste d'autorisation système réduite** : exercée à chaque préflight de machine dédiée (32).
- **Non exercées** : contamination (0, `contaminated` vide), `start_error` / tentative nulle (0, `void_attempts` vide),
  rejeu (0, `replays` vide), égalité (aucune), `keep_cloud_insufficient_evidence` (le tamis est complet).

**La validation explicite par le mainteneur des règles fixées par le coordinateur est DEMANDÉE par le présent rapport**
(l'en-tête du protocole la déclare « pas encore validée »).

## Les trois verdicts (PAT-ADR-0015)

- **Compatibilité** : les cinq candidats exécutent l'explorateur en lecture seule sous le harnais réel (outils `read`, `grep`
  et `glob` seuls utilisés dans les 30 flux, `bundle_modified` faux sur les 30 enregistrements, aucune contamination,
  `ended_by_external_signal` faux sur les 30). Critère machine (supplément de swap par tentative sous 10 Gio) : supplément
  maximal **+1094,7 MiB (1,07 Gio)**, qwen3-coder, PR 83 (suivi de +919,4 MiB, PR 48) ; **rempli**. Pics de swap par candidat
  (`peak_swap_used_mib`) : 4412,9 (muse), 6419,0 (qwen3-coder), 4436,9 (qwen3.6), 4452,9 (qwen3.8 4 bits), 4460,9 (qwen3.8 6 bits)
  MiB, contre 25 à 30 Gio en v1 sur une machine non dédiée.
- **Qualité** (filtre du tamis) : **échec au seuil** : meilleur rappel moyen de fonctions 0,25 < 0,5 et aucun candidat avec une
  précision moyenne de fichiers >= 0,5. Comparaison à une référence cloud : non mesurée (aucune référence cloud sur ces
  6 tâches).
- **Économie** : non mesurée (comparaison non lancée, aucun coût cloud).

Aucune promotion. Conséquence par la règle gelée : « conserver le cloud » pour l'exploration en lecture seule ; la comparaison
(arms A / L / E, 6 tâches de comparaison jamais jouées) n'est pas lancée.

## Limites et inconnus

- **Effet aval d'un rapport local : non mesuré** (pas de comparaison). Le tamis ne mesure que la localisation.
- **Bornes figées** : on ne sait pas si des bornes plus longues (temps ou étapes) laisseraient les modèles conclure ; les
  bornes de 10 min et 25 étapes sont gelées en v2, les changer ouvre une v3.
- **Origine des échecs** (modèle, harnais, moteur, contexte) inconnue ; les paramètres d'échantillonnage effectifs ne sont pas
  enregistrés.
- **Effet du rechargement** sur les deux rapports vides de qwen3-coder : inconnu.
- **Aucune référence d'explorateur cloud** sur ces 6 tâches : leur difficulté n'est pas calibrée.
- 6 tâches ne constituent pas une preuve générale ; la machine, les moteurs (MLX et GGUF) et l'ordre des candidats sont des
  variables non contrôlées.
- Le rapport mécanique donne `exploration_comparison.campaign_conclusion` à `incomplete_campaign` (aucun bras joué) ; la
  conclusion de la campagne est celle de `exploration_screening` (`keep_cloud`).

## Pièces versionnées

Dans [`pat-19-runs/xscreen-1/`](pat-19-runs/xscreen-1/), copiées telles quelles : `ledger-pat-19-xscreen-1.jsonl`,
`results-pat-19-xscreen-1.jsonl`, `report-pat-19-xscreen-1.json`, `envelope-pat-19-xscreen-1.json`, `streams-manifest.json`.
Les 30 flux d'événements omp bruts ne sont pas versionnés (environ 48 Mo, ils embarquent des contenus du dépôt) ; ils restent
sur la machine du mainteneur et `streams-manifest.json` donne leur sha256 et leur taille. Les rapports vides de qwen3-coder
et l'usage des seuls outils `read`, `grep`, `glob` ont été lus dans ces flux bruts hors dépôt. Tous les chiffres de ce document
ont été recalculés à partir des fichiers versés.

## Statut documentaire (R5)

Ce document et les cinq pièces ci-dessus sont les seuls artefacts ajoutés ; un lien est ajouté dans la section « Protocole v2 »
de `pat-19-launcher-v1.md`, une ligne dans le CHANGELOG. Aucun verbe ni option de `foundry_cli.py`, clé de configuration,
constante publique ou table de routage n'a changé ; protocole v2 et configuration v2 inchangés ; aucun code.
