# PAT-19 — Protocole de qualification local-first, version 2 : exploration en lecture seule

PAT-114. Cadre : PAT-ADR-0015 (exploration évaluée seulement par son effet aval, tamis local sans cloud avec une règle écrite d'avance, coût net par tâche acceptée, trois verdicts séparés, aucune promotion, une donnée absente n'est jamais zéro), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, pas de second cadre), FOUNDRY-ADR-0010 (enveloppe d'autorisation), FOUNDRY-ADR-0015 (coût lu dans les journaux de session), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Valeurs validées par le mainteneur le **2026-10-06**. Tout changement d'une coordonnée gelée ci-dessous ouvre une version 3.

Origine : la décision de PAT-110 ([`pat-19-decision-v1.md`](pat-19-decision-v1.md)) conserve le cloud pour l'implémentation autonome (0 tâche acceptée sur 30 en v1) et ouvre l'usage 2 : un explorateur local en lecture seule qui prépare le travail d'un implémenteur cloud. Ce fichier gèle le protocole de cet usage ; il **ne modifie pas** le protocole v1 ([`pat-19-protocol-v1.md`](pat-19-protocol-v1.md)), sa configuration (`pat-19-campaign-v1.json`) ni ses résultats : la v1 reste gelée et reproductible.

**État au gel.** Aucune tentative du corpus v2 n'a eu lieu. Les deux pilotes explorateurs ont été essayés pour de vrai le 2026-10-06 sur un dépôt jouet (hors corpus) avec le `execute_driver` du lanceur, avec l'autorisation du mainteneur : ils sont `verified: true`, preuves dans [`pat-19-preflight-v2-2026-10-06.json`](pat-19-preflight-v2-2026-10-06.json). **Les noms d'outils ont été corrigés par cet essai, avant le gel** : `omp` 18.6.1 a refusé `--tools=read,grep,find,ls` (« Unknown tool in --tools: ls » ; `find` indisponible dans la session) ; l'argv gelé est `--tools=read,grep,glob`, le même trio que l'explorateur cloud (Read/Grep/Glob). L'intention validée par le mainteneur (lecture seule) est inchangée ; le chargeur n'accepte que `read`, `grep`, `glob`.

## 1. Question posée

Pour des correctifs bornés de ce dépôt, un rapport d'exploration produit **avant** le travail par un explorateur en lecture seule (a) localise-t-il les bons fichiers (tamis local, sans cloud), puis (b) réduit-il le coût premium **par tâche acceptée** d'un implémenteur cloud, à acceptation au moins égale (comparaison) ?

## 2. Hérité de la v1, et ce qui change

| Élément | v1 | v2 |
| --- | --- | --- |
| Corpus | 6 tâches de tamis + 6 de comparaison, manifeste `pat-19-corpus-manifest-v1.json` | **identique**, aucun nouveau tirage ; les 6 tâches de comparaison n'ont jamais été jouées |
| Candidats | cinq (clés, quantifications, empreintes, commandes de chargement, `min_context` 65 536) | **identiques** ; Devstral reste inutilisé |
| Machine, bundles, enveloppe, registre, reprise bornée, signaux, isolement, audit de contamination, préflight, pilotes cloud, juge de tests, revue | v1 | **réutilisés** (même code, `local_first_runner`) |
| Ce qui est jugé | tests protégés (le candidat écrit le correctif) | tamis : **localisation** du rapport ; comparaison : **effet aval** (acceptation et prime par tâche acceptée) |
| Bras local | `omp` complet, bundle inscriptible | `omp --tools=read,grep,glob`, bundle **en lecture seule** dans le profil du bac à sable |
| Bornes locales | 20 min, 40 étapes | **10 min, 25 étapes** |
| Parcours | A, B, C, N | A, L, E (section 6) |
| Machine dédiée | note du protocole, relevé du swap | **contrôle refusant** (section 8) |
| Enveloppe | 45 exécutions cloud | jusqu'à **120** (section 7) |

## 3. Coordonnées gelées

| Élément | Valeur |
| --- | --- |
| Tâches du tamis | les 6 du tamis v1 (PR 30, 83, 27, 24, 48, 19) |
| Tâches de la comparaison | les 6 de la comparaison v1 (PR 26, 38, 25, 42, 33, 37), gelées, jamais jouées |
| Candidats | les cinq de la v1 ; un seul est retenu par le tamis |
| Explorateur local | `omp` 18.6.1, `--tools=read,grep,glob`, bundle en lecture seule dans le profil (seul le dossier d'essai de la tentative est inscriptible), bornes **10 minutes et 25 étapes** |
| Explorateur cloud économique (bras E) | Claude Code nu, Haiku 4.5 (effort nul), même rôle en lecture seule et même format de rapport |
| Implémenteur (A, L, E) | Claude Code nu, Sonnet 5.5, effort medium (pilote v1 `cloud_implementer_current`) |
| Revue | Opus 5.5, effort high, au plus 2 corrections (`max_correction_rounds`), identique pour tous les bras (chemin A de la v1) |
| Rapport | un objet JSON `{"files": [chemins relatifs au dépôt], "functions": [{"file", "name"}], "rationale": "..."}`, les trois clés obligatoires |
| Rapport absent ou illisible | **refus** (score 0) ; jamais « nul » (rejouable) sauf si le lanceur a échoué avant que le bras ne tourne |
| Machine dédiée | refus si un processus hors LM Studio, lanceur et système dépasse 2 Gio de mémoire résidente, ou si le pourcentage de mémoire libre de `memory_pressure` est sous 50 % au départ |

Toutes ces valeurs sont des données de [`pat-19-campaign-v2.json`](pat-19-campaign-v2.json) (`bounds`, `rules`, `dedicated_machine`, `drivers`, `candidates`).

## 4. Juge de localisation (déterministe, sans modèle ni réseau)

La vérité terrain de chaque tâche vient du **diff fusionné** (snapshot et SHAs du manifeste), calculée par `foundry.local_first_exploration.ground_truth` :

- **fichiers produit** : fichiers modifiés ou supprimés par le diff, sous les préfixes produit du juge de la v1 (`SOURCE_PREFIXES` : `plugins/foundry/tooling/` et `plugins/foundry/hooks/`), hors `tests/` et hors documentation (`_is_doc` : `.md`, `.json`, `.txt`, `docs/`). C'est la classification que le juge de la v1 utilise déjà, pas une nouvelle. Les fichiers **ajoutés** par le diff ne comptent pas dans le rappel (un explorateur ne peut pas trouver ce qui n'existe pas) mais sont acceptés par la précision ;
- **fonctions** : pour un fichier Python modifié, les fonctions et méthodes (`f` ou `Classe.methode`, classes imbriquées pointées ; une fonction imbriquée appartient à sa fonction englobante) qui **enveloppent une ligne modifiée** (`git diff -U0`, AST au SHA fusionné) et existent au SHA de base. Une ligne hors de toute fonction (niveau module) ne donne que le fichier ; un fichier non Python est jugé au niveau du fichier seulement. Les fonctions qui n'existent pas au SHA de base (nouvelles) ne comptent pas dans le rappel de fonctions.

Mesures par tâche : **rappel de fonctions** (**métrique principale du tamis** : vraies fonctions trouvées / vraies fonctions ; voir la section 5), **rappel de fichiers** (mesuré et rapporté, ne décide plus : fichiers vrais trouvés / fichiers vrais), **précision de fichiers** (fichiers rapportés vrais ou ajoutés / fichiers rapportés ; 0 pour un rapport sans fichier). Le rappel de fonctions est indéfini, non nul, quand le diff ne touche aucune fonction (la tâche est alors exclue de sa moyenne : aucune des 12 tâches n'est dans ce cas) ; un nom rapporté correspond au nom qualifié ou à son dernier composant, dans le même fichier). Un rapport absent, illisible ou de mauvais type vaut 0 partout. La vérité terrain des 12 tâches est versée dans [`pat-19-exploration-truth-v2.json`](pat-19-exploration-truth-v2.json) (non vide pour chacune, testé sur les vrais diffs fusionnés ; le lanceur la recalcule à partir du dépôt).

## 5. Tamis local v2 (sans cloud, règle fixée d'avance)

Chaque candidat explore chacune des 6 tâches du tamis, une tentative bornée (10 min, 25 étapes), jugée sur la localisation. Le préflight machine, contrôle de machine dédiée compris, est refait avant chaque tâche. Aucune exécution cloud n'est possible dans ce mode.

Règle (calculs exacts sur des fractions) :

1. sont éligibles les candidats dont la **précision moyenne de fichiers est ≥ 0,5** ;
2. est retenu l'éligible de **rappel moyen de fonctions le plus élevé** sur les 6 tâches (**métrique principale**) ; à égalité, la **durée totale la plus courte** ; à égalité de durée, non résolu ;
3. **ARRÊT sur « conserver le cloud »** si aucun candidat n'atteint la précision 0,5, ou si le rappel moyen de fonctions du candidat retenu est **< 0,5** (0,5 inclus : exactement 0,5 est retenu). Le rappel de fichiers reste mesuré et rapporté mais ne décide plus.

**Pourquoi le rappel de fonctions décide (décision du mainteneur du 2026-10-06, avant le gel, après lecture de la vérité terrain).** Chaque tâche a un seul fichier produit et 5 des 6 tâches du tamis partagent `trackers/linear.py` : le rappel de fichiers est quasi trivial (citer `linear.py` suffit) et ne départagerait pas les candidats. Un candidat de rappel de fichiers parfait et de rappel de fonctions nul n'est donc **pas** retenu face à un candidat de rappel de fonctions positif. Une tâche dont la vérité terrain n'a aucune fonction est exclue de la moyenne (aucune aujourd'hui) ; si aucune tâche n'a de fonction, aucun candidat n'est retenu (`no_function_in_the_ground_truth`).

**Tentative contaminée** (règle v2, gelée maintenant et non après les résultats) : elle compte pour **0 sur toutes les mesures** (rappel de fonctions, rappel de fichiers, précision) sur cette tâche ; un rapport refusé de même, et le tamis n'est jamais bloqué par un seul signalement. C'est un écart voulu avec la v1, où une contamination rendait la tâche indécise et le tamis incomplet. La contamination reste consignée (`report.contaminated`). Une tentative cassée par le lanceur ou interrompue avant le jugement reste, comme en v1, nulle (rejouée une fois) ; une tentative qui a reçu un verdict n'est jamais rejouée.

Aucun candidat n'est retenu tant que chacun n'a pas un enregistrement décidé pour chaque tâche (`incomplete_screening`).

## 6. Comparaison v2 : A, L, E sur les 6 tâches de comparaison

Chaque tâche est jouée dans les trois bras depuis la même base Git, mêmes critères d'acceptation, même revue.

- **A** : l'implémenteur cloud courant, sans rapport ; juge de tests, revue Opus, au plus 2 corrections (chemin A de la v1) ;
- **L** : comme A, avec le rapport du candidat local retenu **ajouté à l'énoncé** (après le pied d'énoncé du lanceur, dans le TASK.md de l'implémenteur et de ses corrections, jamais dans celui du relecteur). L'exploration est produite d'abord, localement, **dans la même enveloppe**. Le candidat doit être celui que le tamis a retenu ;
- **E** : comme A, avec le rapport d'un explorateur cloud économique (Haiku 4.5, même rôle en lecture seule, même format de rapport), produit d'abord.

Un explorateur qui ne rend aucun rapport utilisable (refus) laisse l'implémenteur travailler sur l'énoncé simple, le coût de l'explorateur restant compté. Un explorateur contaminé, ou coupé deux fois, laisse le bras indécis sur cette tâche (aucun implémenteur dépensé).

**Métrique** : tokens premium totaux **par tâche acceptée**, explorateur compris : somme non pondérée des quatre classes de tokens facturables (v1) sur tous les modèles ; **Haiku compte comme premium**, les tokens locaux non ; le temps mural local est rapporté (durée de l'exploration locale et durée de chaque bras). Les trois verdicts restent séparés (PAT-ADR-0015) :

- **compatibilité** (L) : critère machine de la v1 (aucun arrêt sur un signal extérieur, swap supplémentaire sous 10 Go) ; `unavailable` si le swap n'est pas mesuré ; E n'a pas de travail local ;
- **qualité** : le taux d'acceptation du bras est **au moins** celui de A sur les mêmes tâches. Les tâches indécises bornent le compte : `pass` seulement si cela tient même au pire, `fail` seulement si cela échoue même au mieux, sinon `unavailable` ;
- **économie** : prime par tâche acceptée du bras **≤ 0,85 × celle de A**. `unavailable` (jamais zéro, jamais une estimation) si une tâche comparée est indécise, si du travail dépensé est inconnu au registre, si un total premium est inconnu ou si un bras n'a accepté aucune tâche.

**Règle** : L est retenu si la qualité **et** l'économie **et** la compatibilité passent, sur les 6 tâches. Une campagne partielle (plafond, panne d'outil, interruption) ne conclut rien (`inconclusive`) : v2 n'a pas d'arrêt séquentiel. Un `fail` sur la campagne complète donne `keep_cloud`. **E contre A** est calculé avec la même règle, à titre **informatif** : il ne produit ni décision ni recommandation. Rien n'est promu (`promotion: false`).

## 7. Enveloppe (FOUNDRY-ADR-0010)

Fichier de l'opérateur, jamais écrit par le lanceur. Les modes v2 sont `screen_exploration` et `compare_exploration`. Valeurs recommandées (consignées en données dans `envelope_recommended` de la configuration), avec leur justification :

| Enveloppe | Plafonds | Pourquoi |
| --- | --- | --- |
| tamis | 0 exécution cloud, 0 token premium, **36 000 s** | 5 candidats × 6 tâches × 10 min au plus = 18 000 s ; le double couvre le rejeu d'une tentative nulle et les préflights |
| comparaison | **120 exécutions cloud**, **60 000 000 tokens premium**, **100 000 s** | par tâche et par bras, au plus 1 implémenteur + 2 corrections et 3 revues = 6 exécutions : 3 bras × 6 tâches × 6 = 108, plus 6 explorations Haiku = 114 ≤ 120 (le plafond tient le pire cas ; le minimum est de 42) ; 60 M = 500 000 par exécution du plafond (la v1 prévoyait environ 444 000 : 20 M pour 45), le volume réel étant inconnu avant mesure : un plafond arrête la campagne, ce n'est pas une estimation ; 100 000 s (27,8 h) couvrent 114 exécutions d'une dizaine de minutes et 6 explorations locales de 10 min, sous la somme des bornes du pire cas |

Le tamis n'utilise aucun cloud. Les plafonds sont cumulés entre lancements (registre) ; une donnée de tokens inconnue arrête les exécutions cloud (`premium_tokens_unmeasurable`).

## 8. Machine dédiée (nouveau contrôle du préflight)

Appliqué par le préflight de `screen-exploration` (avant chaque tâche) et de `compare-exploration` quand le bras L est joué (A et E ne chargent aucun modèle local). Commandes en lecture seule déjà autorisées : `ps -axo rss=,command=` et `memory_pressure`. Refus si :

- un processus **hors liste d'autorisation** a plus de **2 Gio** de mémoire résidente (strictement) ;
- le pourcentage de mémoire libre (`System-wide memory free percentage`) est **< 50 %** au départ ;
- une des deux sorties est indisponible (inconnu n'est jamais un succès).

La liste d'autorisation est une donnée de la configuration (`dedicated_machine.allowed_command_patterns`, expressions régulières cherchées dans la ligne de commande) : `lm_studio` (`/LM Studio\.app/`, `/\.lmstudio/`, `lms`), `launcher` (`foundry[._]local_first_runner`, `local_first_runner\.py`), `system` (`/System/`, `/usr/libexec|sbin|bin/`, `/sbin/`, `/Library/Apple/`, `kernel_task`). Les valeurs observées (mémoire libre, mémoire résidente par catégorie autorisée, cinq plus gros autres processus **par nom seulement**, jamais un argument de ligne de commande) sont inscrites au registre (entrée `preflight`) et sur chaque enregistrement de tentative (`machine.dedicated`). Limites : le lanceur lancé autrement que par `python3 -m foundry.local_first_runner` n'est pas reconnu comme tel ; la session du coordinateur elle-même (un processus d'agent) peut dépasser 2 Gio et refuser.

## 9. Usage

Depuis `plugins/foundry/tooling`, mêmes arguments que la v1 (`--envelope`, `--state-dir`, `--work-root`, `--repo` : clone complet, `--snapshot`, `--manifest`) :

```
python3 -m foundry.local_first_runner preflight --campaign <cfg-v2> --candidate <id> [--dedicated] [--dry-run]
python3 -m foundry.local_first_runner screen-exploration  ... --candidate <id> [<id> ...]
python3 -m foundry.local_first_runner compare-exploration ... --candidate <id> [--paths A,L,E] [--screening-campaign <id>]
python3 -m foundry.local_first_runner report --campaign <cfg-v2> --results <results-<campagne>.jsonl>
```

La configuration v2 (schéma `foundry.local-first-campaign.v2`) n'est acceptée que par les modes v2 et inversement (refus, code 2). Le rapport v2 contient `exploration_screening` (table par candidat, rappel moyen de fonctions en premier : rappel moyen de fonctions, précision et rappel moyens de fichiers, durée totale, refus, contaminations, détail par tâche ; candidat retenu ou arrêt) et `exploration_comparison` (bras L et E contre A : trois verdicts, détail de l'économie, localisation de l'explorateur par tâche, décision). Le détail du lanceur est dans [`pat-19-launcher-v1.md`](pat-19-launcher-v1.md), section « Protocole v2 ».

## 10. Essais réels des pilotes (2026-10-06, dépôt jouet `src/calc.py`, hors corpus)

1. **`local_explorer`** : Qwen3.6-35B-A3B MLX 4 bits chargé avec sa `load_command` épinglée, bac à sable actif, bundle en lecture seule (`workdir_writable=False`), refus du HOME : sortie 0, 14,0 s, 4 étapes (read 2, grep 1, glob 1), bundle inchangé (`git status` identique avant et après), rapport JSON du message final lu par `load_report` (source `final_message`), score sur la vérité terrain jouet : rappel de fichiers 1,0, précision 1,0, rappel de fonctions 1,0. Premier argv (`find`, `ls`) refusé par `omp`, voir ci-dessus ;
2. **`cloud_explorer_economy`** (Haiku 4.5, sans bac à sable, HOME réel, environnement R6) : sortie 0, 15,1 s, outils de l'événement `init` = `Bash`, `Read` (Claude Code 2.1.285 n'annonce pas d'outil Grep ni Glob séparé avec cet argv ; sous-ensemble de la liste d'autorisation), outils utilisés Read 3 et Bash 1, bundle inchangé, rapport lu depuis le message final, score jouet 1,0 / 1,0 / 1,0 ; tokens lus par le lanceur égaux au `result.usage` de l'hôte (entrée 42, cache lu 54 945, cache écrit 6 902, sortie 1 274).

Les pilotes `cloud_implementer_current` et `cloud_reviewer` sont repris tels quels de la v1 (argv et preuves de PAT-111 inchangés).

## 11. Limites (le rapport les dira)

- **Bundle inchangé, contrôlé** : le lanceur relève `git status --porcelain` du bundle avant et après chaque exploration (locale et cloud) ; un bundle modifié ou illisible **refuse** l'exploration (score 0, jamais rejouée, rapport non utilisé, `exploration.bundle_modified` consigné). Pour l'explorateur cloud c'est une observation, pas une garantie : la lecture seule n'est pas imposée.
- **Lecture seule du bras cloud E non imposée** : Claude Code ne s'authentifie pas sous `sandbox-exec` ; l'explorateur Haiku est en lecture seule par consigne et par jeu d'outils (ni `Edit` ni `Write`), mais `Bash` reste ouvert (règles de refus de la v1 au mieux, audit après exécution) ; l'essai a observé un bundle inchangé, sans garantie. Les limites d'exposition des bras cloud de la v1 s'appliquent entières à L et E (implémenteur, relecteur, explorateur E).
- **Source du rapport** : les jeux d'outils épinglés n'ont aucun outil d'écriture ; le prompt demande donc le JSON comme message final et le lanceur le lit dans le flux ; un `report.json` écrit par le bras dans son dossier d'essai est aussi accepté. Les noms d'outils d'`omp` et la forme du message final ont été épinglés par l'essai du 2026-10-06.
- **Vérité terrain mince** : chaque tâche modifie un seul fichier produit, parfois 1 à 4 fonctions ; la précision de fichiers ≥ 0,5 sanctionne donc tout rapport qui cite plus de deux fichiers par fichier vrai. C'est la règle validée par le mainteneur, telle quelle.
- **Corpus** : 6 tâches de tamis et 6 de comparaison, non indépendantes (`limits` du manifeste : branche empilée, jumelles, 8 des 12 tâches touchent `tests/test_linear_tracker.py`) ; un échantillon de 6 tâches n'est pas une preuve statistique générale.
- **Rapport non vérifié par le juge de tests** : le tamis ne mesure que la localisation, pas l'effet aval ; seul le coût net par tâche acceptée de la comparaison dit si l'exploration vaut son prix.
- **Un explorateur contaminé en comparaison** laisse le bras indécis (et non compté pour zéro comme au tamis), car on ne peut pas attribuer son effet aval.
- **Ordre des tâches** : les bras A, L, E d'une tâche sont joués à la suite ; un effet de cache côté hôte entre bras n'est pas isolé.

## 12. Statut documentaire (AGENTS.md R5)

Artefacts documentés ici : les modes et sous-commandes `screen-exploration` et `compare-exploration` et l'option `preflight --dedicated` de `foundry.local_first_runner` ; le schéma de configuration `foundry.local-first-campaign.v2` (`pat-19-campaign-v2.json` : `bounds.explorer_max_*`, `prompts.explore`, `rules.exploration_screening`, `rules.exploration_comparison`, `dedicated_machine`, `exploration.report_render_limits`, `envelope_recommended`, pilotes `local_explorer` et `cloud_explorer_economy`) ; les types de pilote `local_explorer` et `cloud_explorer` ; les chemins de résultats `XS`, `L`, `E` et le segment `explore` ; le module `foundry.local_first_exploration` (vérité terrain, juge de localisation, règles) ; le fichier de vérité terrain des 12 tâches ; le contrôle de machine dédiée. Documentés dans ce fichier et dans la section « Protocole v2 » de `pat-19-launcher-v1.md`. Aucun verbe ni option de `foundry_cli.py`, clé de configuration produit, constante publique de routage ni table de routage n'a changé ; aucune coordonnée du protocole v1 n'est modifiée. **Documenté et non appliqué mécaniquement** (dit explicitement) : la lecture seule de l'explorateur cloud, la lecture seule garantie du bundle pour l'explorateur cloud (contrôle `git status` après coup seulement), l'indépendance des tâches. L'application mécanique de R5 reste celle de FOUNDRY-123.
