# PAT-19 — Lanceur de comparaison, version 1

PAT-108. Cadre : PAT-ADR-0015 (coût net par tâche acceptée, trois verdicts séparés, aucune
promotion), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, arrêt séquentiel, pas de
matrice ni de second cadre), FOUNDRY-ADR-0010 (enveloppe d'autorisation vérifiée avant toute
frontière hôte), FOUNDRY-ADR-0015 (coût lu dans les journaux de session de l'hôte : une donnée
inconnue reste inconnue, jamais zéro), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Protocole :
[`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) ; corpus et juge : [`pat-19-corpus-v1.md`](pat-19-corpus-v1.md).

C'est un outil de campagne : il ne touche ni routage, ni mappings, ni rôles, ni valeurs par défaut,
ne charge aucun modèle (aucune commande `lms load`), n'appelle ni tracker ni réseau, et ne promeut
rien. Ce ticket ne l'exerce qu'avec des parcours **factices** (mode à blanc) ; les exécutions réelles
sont celles de PAT-109, avec la confirmation du mainteneur.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `../../tooling/foundry/local_first_runner.py` | Le lanceur (`python3 -m foundry.local_first_runner`) ; réutilise `local_first_corpus` (bundles, juge) et `cost_attribution` (journaux de session) |
| `pat-19-campaign-v1.json` | Configuration de campagne figée : coordonnées machine, bornes, règles, pilotes de parcours, candidats |
| `../../tests/test_local_first_runner.py` | Tests déterministes (parcours factices, machine injectée, sondes de confinement) |

## Outil

Depuis `plugins/foundry/tooling` :

```
python3 -m foundry.local_first_runner preflight --campaign <cfg> --candidate <id> [--dry-run]
python3 -m foundry.local_first_runner screen  --campaign <cfg> --envelope <env> --state-dir <dir> --work-root <dir> \
    --repo <clone complet> --snapshot <snapshot> --manifest <manifeste> --candidate <id> [<id> ...] \
    [--harness local_harness|neutral_harness] [--dry-run] [--sandbox]
python3 -m foundry.local_first_runner compare --campaign <cfg> --envelope <env> --state-dir <dir> --work-root <dir> \
    --repo <clone complet> --snapshot <snapshot> --manifest <manifeste> --candidate <id> [--paths A,B,C,N] \
    [--harness ...] [--dry-run] [--sandbox]
python3 -m foundry.local_first_runner report --campaign <cfg> --results <results-<campagne>.jsonl>
```

Codes de sortie : 0 terminé, 2 refus (pas d'enveloppe, enveloppe invalide, préflight refusé, pilote non
vérifié, dossier de travail dans le checkout…), 3 plafond d'enveloppe atteint.

- `--work-root` est un dossier jetable **hors du checkout de développement** (refusé sinon). Chaque
  tentative y reçoit un bundle neuf (`attempt-NNNN-…/bundle`) et un dossier d'essai
  (`scratch/`), supprimés ensuite. Les flux d'événements bruts sont conservés sous
  `<state-dir>/streams/`.
- `screen` ne fait que des tentatives locales (candidat × tâche du tamis) et le juge : aucune
  exécution cloud n'est possible dans ce mode (`EnvelopeError`, testé). Il accepte plusieurs
  `--candidate` ; le préflight est refait pour chacun, donc le mainteneur charge le modèle suivant
  entre deux candidats (le lanceur ne charge rien) et relance avec les candidats restants.
- `compare` joue chaque tâche de comparaison dans les parcours demandés, dans l'ordre. `N` est la
  tentative locale sous le harnais neutre sans validation cloud (coût : du temps machine seulement).
- `--dry-run` n'accepte que des pilotes marqués `"fake": true`, utilise des faits machine
  canoniques (aucune commande lancée) et n'applique pas le bac à sable sauf avec `--sandbox`. Un
  vrai lancement refuse les pilotes `fake` et tout pilote sans `"verified": true`.

## Parcours

Chaque tentative part d'un bundle **neuf** de PAT-107 (jamais un bundle jugé : le juge y écrit les
tests protégés ; le juge refuse un bundle déjà jugé et le lanceur ne remet pas un bundle jugé ou
réutilisé). L'énoncé est `TASK.md` suivi du `statement_footer` de la configuration, identique pour
tous les parcours (interdit de créer un environnement virtuel, `node_modules`, ou un fichier de
configuration de test).

- **A** : implémenteur cloud courant, juge mécanique, revue indépendante (sur un bundle neuf portant
  seulement le correctif, jamais le résultat du juge), puis corrections tant que la revue bloque ou que le
  juge refuse, au plus `bounds.max_correction_rounds` corrections. Une correction repart d'un bundle
  neuf sur lequel le correctif précédent (pris **avant** le juge) est réappliqué, avec un retour
  (`feedback.md` : constats de la revue, ou note et comptes du juge, jamais le contenu des tests protégés).
- **B** : comme A avec l'implémenteur du palier economy.
- **C** : une seule tentative locale bornée (`bounds.local_max_seconds` = 1200 s, durée imposée par
  l'arrêt du groupe de processus ; `bounds.local_max_steps` = 40 étapes, comptées dans le flux
  d'événements quand il les expose, sinon inconnues et non imposées), le juge ; si `ACCEPTED`,
  revue cloud ; si refus du juge ou blocage de la revue, reprise par le parcours A sur un bundle
  neuf dont le coût s'ajoute. Une tentative locale échouée est consignée à part (`local_outcome`) :
  ce n'est ni un échec ni une escalade au sens de FOUNDRY-ADR-0006.
- **N** : la tentative locale sous `neutral_harness`, jugée, sans cloud. Le harnais est une
  coordonnée de chaque résultat local (`local.harness`, `local.harness_kind`).

Arrêt anticipé (règle du protocole, évaluée après chaque tâche) : moins de `min_local_successes`
tentatives locales réussies **possibles** sur les tâches prévues (`fewer_than_min_local_successes`) ;
travail premium cumulé de C supérieur ou égal à celui de A quand les deux sont mesurés
(`premium_c_not_below_a`). Les arrêts sont consignés (`stop`).

## Enveloppe d'autorisation (FOUNDRY-ADR-0010, adaptée à un abonnement)

Fichier JSON donné par l'opérateur ; le lanceur ne l'écrit jamais et refuse de démarrer sans lui.

```json
{"schema": "foundry.local-first-envelope.v1", "campaign_id": "pat-19-pilot-1",
 "expires_on": "2026-12-31", "allowed_modes": ["screen", "compare"],
 "caps": {"cloud_executions": 45, "premium_tokens": 20000000, "wall_clock_seconds": 90000}}
```

Refus : fichier absent ou invalide, schéma inconnu, identifiant non simple, date dépassée, mode non
autorisé, plafond manquant ou négatif. `screen` exige une enveloppe aussi (`cloud_executions` peut être
0). Les plafonds sont des plafonds de **campagne** (cumulés dans le registre) :

- exécutions cloud : une exécution est inscrite dans le registre **avant** de démarrer
  (`cloud_started`, avec son identifiant de session) puis le plafond est vérifié ;
- tokens premium : somme des quatre compteurs facturables (entrée non mise en cache, entrée lue en
  cache, entrée écrite en cache, sortie) des exécutions réglées ; l'exécution en cours peut dépasser le
  plafond (le lanceur s'arrête ensuite) ; si les tokens d'une exécution sont inconnus, la campagne
  s'arrête (`premium_tokens_unmeasurable`) : un plafond ne se vérifie pas sur une donnée inconnue ;
- durée : somme des durées murales (locales et cloud) ; elle borne aussi le délai de chaque exécution.

Le registre est `<state-dir>/ledger-<campagne>.jsonl`, en ajout seul (`session_started` avec le
sha256 de l'enveloppe, `preflight`, `cloud_started`, `settled`, `stopped`).

## Préflight (le lanceur ne charge aucun modèle)

Seules les commandes de la liste `READ_ONLY_COMMANDS` peuvent être lancées (toute autre est refusée
par `default_run`, y compris `lms load`) : `sysctl` (puce, mémoire, swap), `sw_vers`, `memory_pressure`,
`lms version`, `lms runtime ls`, `lms ps --json`, `ps`. Il refuse quand : une coordonnée gelée de
`frozen_machine` diffère (puce, mémoire, macOS, LM Studio, moteur MLX), un fait est indisponible,
un autre modèle que celui attendu est chargé ou le modèle attendu ne l'est pas (`lms ps` doit lister
exactement l'identifiant), la place disque est sous le minimum. Il relève le swap et la pression
mémoire de départ. `screen` et `compare` refusent de lancer (code 2, aucun pilote lancé) si le préflight échoue.
Les empreintes des poids, le gabarit et les paramètres de génération sont à consigner par le préflight
de PAT-109 (non faits ici).

## Configuration de campagne (`pat-19-campaign-v1.json`)

Clés : `frozen_machine`, `server_process_pattern` (expression pour la mémoire du serveur), `bounds`,
`statement_footer`, `prompts` (`implement`, `correct`, `review`), `rules` (`screening`, `comparison`),
`drivers`, `candidates` (identifiant → `model`). Un pilote est une donnée :

| Clé | Sens |
| --- | --- |
| `kind` | `local_harness`, `neutral_harness`, `cloud_implementer`, `cloud_reviewer` |
| `argv` | commande ; variables `{workdir}` `{statement_file}` `{model}` `{prompt}` `{max_seconds}` `{max_duration}` `{max_steps}` `{session_id}` `{review_file}` `{feedback_file}` `{scratch}` |
| `verified` | un vrai lancement refuse un pilote non vérifié ; PAT-109 doit le tester et l'épingler |
| `fake` | pilote de test (mode à blanc seulement) |
| `home`, `network` | `isolated`/`real`, `loopback`/`open` ; un pilote local est toujours `isolated` + `loopback` (refusé au chargement sinon) |
| `env_allow`, `home_files`, `extra_write` | variables transmises en plus (jamais `FOUNDRY_*`, jeton, clé, secret, agent SSH), fichiers placés dans le HOME isolé, dossiers inscriptibles en plus |
| `stream` | `{"format": "omp-json" \| "none", "speed_usage_keys": …}` |
| `session_log` | `{"host": "claude", "projects_dir": "~/.claude/projects"}` (pilotes cloud) |

Le pilote `local_harness` est la commande `omp` (18.4.10) éprouvée le 2026-10-05 ; le harnais neutre
et les trois pilotes cloud (Claude Code en mode non interactif) sont déclarés `"verified": false`.

## Mesures et résultats

Un enregistrement par (tâche, parcours, tentative) dans `<state-dir>/results-<campagne>.jsonl`
(`foundry.local-first-result.v1`) : `task` (`pr`, `issue`, `set`), `path` (`S` pour le tamis, `A`, `B`,
`C`, `N`), `segment` (`local`, `cloud`, `takeover`), `attempt`, `outcome`, `judge` (verdict et comptes),
`accepted` (vrai seulement quand le parcours se termine accepté : tests protégés verts **et** revue sans
blocage ; nul sinon), `review` (`rounds`, `verdicts`), `wall_seconds`, `cloud_executions`, `premium`
(`by_role` par classe de token, `billing_total`), `local` (harnais, candidat, `steps`, `stream_tokens`,
vitesses, `timed_out`, `step_limit_hit`, code et signal de sortie, `ended_by_external_signal`),
`machine` (`before`/`after` : `swap_used_mib`, `pressure_free_percent`, `server_rss_kib`), `unknown`
(raison de chaque donnée absente). Une donnée absente est `null` avec sa raison, jamais 0.

- **Tokens premium** : chaque exécution cloud reçoit un identifiant de session généré par le lanceur
  (`--session-id`) ; le journal de session de l'hôte est retrouvé par cet identifiant (jamais par
  fenêtre de temps) et lu par `cost_attribution.read_host_log`. Journal absent, ambigu, illisible ou d'une
  autre session : tous les compteurs de l'exécution sont inconnus. La classe de raisonnement que l'hôte ne
  rapporte pas est inconnue. Rôles : `implementer`, `corrector`, `reviewer`.
- **Étapes** : comptées dans le flux d'événements `omp` (`tool_execution_start`) ; inconnues pour un flux
  `none`. **Vitesses** de préremplissage et de génération : lues seulement si `speed_usage_keys` déclare
  les champs du flux ; sinon inconnues (aucune vitesse n'est déduite de la durée murale).
- **Mémoire du serveur** : somme du RSS des processus dont la ligne de commande correspond à
  `server_process_pattern` ; inconnue sinon. **Swap** et **pression mémoire** avant et après chaque
  tentative locale. « Interrompu pour la mémoire » est approché par une sortie sur un signal que le lanceur
  n'a pas envoyé.
- Non mesuré ici : interventions humaines imprévues (à consigner à la main), empreintes des poids.

## Rapport et règles préenregistrées

`report` agrège et applique les règles du protocole, rend trois verdicts **séparés** par parcours
comparé (`pass`, `fail` ou `unavailable`) et ne promeut jamais (`"promotion": false`) :

- **tamis** : le candidat qui fait accepter le plus de tâches ; à égalité la durée totale la plus courte ;
  sous `min_accepted` (2) aucun candidat, « conserver le cloud » ; égalité de durée : non résolue ;
- **compatibilité** (C) : aucune tentative interrompue sur un signal extérieur et swap supplémentaire sous
  `extra_swap_gib_max` (10 Go) ; swap non mesuré : `unavailable` ;
- **qualité** : toutes les tâches acceptées et pas plus de tours de revue au total que A ;
- **économie** : travail premium total inférieur d'au moins 25 % à A **et** durée totale au plus 2 fois A ;
  `unavailable` si le travail premium d'un des deux parcours n'est pas mesurable ;
- décision : `retained` (recommandation B si B suffit, sinon C) seulement si les trois verdicts passent
  sur le nombre de tâches prévu ; `keep_cloud` sur un échec ou un arrêt anticipé ; sinon `inconclusive`.

Le rapport ne dit pas qu'un échantillon de 6 tâches est une preuve statistique générale : il ne l'est pas.

## Isolement du candidat (pendant sa tentative)

Appliqué à chaque pilote lancé par le lanceur (`execute_driver`) :

- **Dépôt** : bundle PAT-107 (un commit racine, sans lien avec le dépôt de développement), hors du
  checkout ; ni les tests protégés, ni le SHA fusionné, ni les seuils n'y figurent (testé).
- **Bac à sable macOS** (`sandbox-exec`, profil généré, `sandbox_profile`) : écriture interdite
  hors du bundle et du dossier d'essai (plus `extra_write` pour un pilote cloud), lecture interdite du
  checkout de développement et du dossier d'état (registre, enveloppe, seuils), réseau limité à la
  boucle locale pour un pilote local. Un vrai lancement exige `sandbox-exec` (refusé ailleurs).
- **Environnement** : liste blanche (`PATH`, `LANG`, `LC_ALL`), HOME et XDG isolés dans le dossier
  d'essai, aucune variable `FOUNDRY_*`, aucun jeton, aucun agent SSH ; un pilote cloud garde le vrai
  HOME (identité OAuth de Claude Code, AGENTS.md R6) et ne reçoit que ce que `env_allow` nomme ;
  entrée standard fermée ; groupe de processus tué à la borne de durée ou d'étapes.
- **Sondes de confinement** testées sous `sandbox-exec` (écriture hors dossier, lecture des seuils,
  connexion non bouclée refusées par `EPERM`, boucle locale permise, variable canari absente) ; ignorées
  explicitement avec leur raison là où `sandbox-exec` est indisponible (CI Linux), où le profil généré
  et la liste blanche d'environnement restent testés.

Ce que l'isolement **n'impose pas** : le candidat lit le reste du disque (le profil ne refuse que les
chemins nommés), un pilote cloud a le réseau ouvert et l'écriture dans `~/.claude`, le binaire du
harnais et sa configuration réelle ne sont pas vérifiés ici (PAT-109), et le code produit reste
importé dans le processus de test (limite du juge). Le suivi du tracker et des secrets repose sur la
liste blanche d'environnement et sur le refus de lecture, pas sur une preuve d'absence.

## Statut documentaire (AGENTS.md R5)

Artefacts documentés ici : la surface CLI de `foundry.local_first_runner`, la configuration de campagne
(`pat-19-campaign-v1.json`), le format d'enveloppe et de registre, le schéma des résultats, les règles du
rapport et le périmètre de l'isolement. Aucune constante publique, option de `foundry_cli.py`, clé de
configuration produit ni table de routage n'a changé. L'application mécanique de R5 reste celle de
FOUNDRY-123.
