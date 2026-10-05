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

Le protocole a été amendé sur place avant tout essai (candidat 2, trois téléchargements) ; le mainteneur
l'a validé le 2026-10-05. La note datée en tête de [`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) en
fait l'historique ; aucun essai n'avait eu lieu et aucun fichier v2 n'existe.

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

Codes de sortie : 0 terminé, 2 refus ou erreur d'outil (pas d'enveloppe, enveloppe invalide, préflight
refusé, pilote non vérifié, dossier de travail dans le checkout, tentative déjà consignée, état mêlant
essai à blanc et réel, candidat inconnu, bac à sable impossible à appliquer, ligne tronquée dans le
registre ou les résultats, rapport sous une autre configuration ou sans registre…), 3 plafond d'enveloppe
atteint. Une interruption (Ctrl-C, SIGTERM, SIGHUP) pendant `screen` ou `compare` sort avec le code du
signal après avoir consigné la tentative coupée et l'arrêt (voir « Tentative interrompue » pour ce qui est
couvert et ce qui ne l'est pas).

`report` lit **obligatoirement** le registre `ledger-<campagne>.jsonl` placé à côté du fichier de
résultats (même dossier d'état, même identifiant de campagne) : sans lui, il refuse (code 2). Il n'y a pas
d'option pour s'en passer.

- `--work-root` est un dossier jetable **hors du checkout de développement** (refusé sinon). Chaque
  tentative y reçoit un bundle neuf (`attempt-lLL-NNNN-…/bundle`, où `LL` est le rang du lancement dans le
  registre de la campagne : un relancement ne réutilise jamais un nom, donc n'écrase pas le flux d'une
  tentative tuée par un lancement précédent) et un dossier d'essai
  (`scratch/`), supprimés ensuite. Les flux d'événements bruts sont conservés sous
  `<state-dir>/streams/`.
- `screen` ne fait que des tentatives locales (candidat × tâche du tamis) et le juge : aucune
  exécution cloud n'est possible dans ce mode (`EnvelopeError`, testé). Il accepte plusieurs
  `--candidate` ; le préflight est refait pour chacun, donc le mainteneur charge le modèle suivant
  entre deux candidats (le lanceur ne charge rien) et relance avec les candidats restants. Le
  préflight machine est refait avant **chaque tâche**.
- **Pas de reprise** : le lanceur refuse de démarrer une tentative dont la clé (parcours, tâche, jeu,
  candidat pour une tentative locale, segment, numéro) a déjà un enregistrement, et `report` refuse un
  fichier qui en contient deux. Une tentative coupée (panne d'outil, Ctrl-C, SIGTERM) est consignée elle
  aussi et ne se rejoue pas. Une tentative **tuée** (SIGKILL, coupure de courant) ne laisse pas de
  clé : voir « Tentative interrompue ». Une campagne interrompue ou bloquée ne se rejoue pas dans le même état : la
  **seule** sortie est un nouvel identifiant de campagne **et** une nouvelle enveloppe, données par le
  mainteneur (donc un registre et un fichier de résultats distincts). Choix assumé, plus simple et plus sûr
  qu'une reprise par saut des tentatives terminées : un candidat rejoué ne peut pas gagner par répétition
  (« une tentative locale », protocole). Une panne d'outil ou une interruption pendant le tamis rend le
  tamis incomplet : il faut une nouvelle campagne et y rejouer **tous** les candidats, pas seulement
  celui qui a été coupé.
- **Le dossier d'état est une entrée de confiance de l'opérateur** : le registre et les résultats sont
  rangés par `--state-dir`, donc relancer la même enveloppe dans un autre dossier d'état remettrait ses
  plafonds à zéro. Le lanceur ne peut pas le détecter sans un état global qu'il n'a pas (non imposé) :
  l'opérateur garde **un seul dossier d'état par identifiant de campagne**, et ne réutilise jamais une
  enveloppe ailleurs. Voir « Confiance dans les fichiers d'état ».
- `compare` joue chaque tâche de comparaison dans les parcours demandés, dans l'ordre. `N` est la
  tentative locale sous le harnais neutre sans validation cloud (coût : du temps machine seulement).
  `--paths A,B` n'a pas besoin du modèle local : aucun préflight machine n'est lancé (seuls `C` et `N`
  l'exigent, avant chaque tâche).
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

- exécutions cloud : le plafond est vérifié, puis l'exécution est inscrite dans le registre
  (`cloud_started`, avec son identifiant de session), puis elle démarre (ordre du code) ; la date
  d'expiration de l'enveloppe est revérifiée avant **chaque** exécution cloud, pas seulement au chargement ;
- tokens premium : somme des quatre compteurs facturables (entrée non mise en cache, entrée lue en
  cache, entrée écrite en cache, sortie) des exécutions réglées ; l'exécution en cours peut dépasser le
  plafond (le lanceur s'arrête ensuite) ; si les tokens d'une exécution sont inconnus, la campagne
  s'arrête (`premium_tokens_unmeasurable`) : un plafond ne se vérifie pas sur une donnée inconnue.
  `settled` porte l'identifiant de session de son `cloud_started` ; un `cloud_started` sans `settled`
  apparié (lanceur tué, interruption, exception) rend les tokens inconnus : aucune nouvelle exécution
  cloud ne démarre, y compris après un redémarrage (jamais « 0 token » par défaut). La campagne est alors
  terminée pour le cloud : il faut un nouvel identifiant de campagne et une nouvelle enveloppe ;
- durée : somme des durées murales (locales et cloud) ; elle borne aussi le délai de chaque exécution.

Une erreur de configuration ne brûle pas la campagne : la disponibilité de `sandbox-exec` et le profil
de chaque pilote du mode demandé sont vérifiés à la construction du lanceur (avant toute tentative), et le
profil d'une exécution cloud est généré avant son inscription au registre.

Le registre est `<state-dir>/ledger-<campagne>.jsonl`, en ajout seul et synchronisé sur disque à chaque
ligne, comme le fichier de résultats (`session_started` avec les sha256 de
l'enveloppe, de la configuration de campagne et du manifeste, `preflight`, `cloud_started`, `settled`,
`attempt_started`, `stopped`). Une ligne tronquée ou illisible dans l'un des deux fichiers est un refus net qui nomme le
fichier et la ligne : cet état n'est plus fiable, il faut une nouvelle campagne. Chaque ligne du registre et chaque enregistrement de résultat porte `dry_run` ; un état qui
contient des lignes de l'autre nature est refusé (jamais d'exécution à blanc comptée contre un plafond
réel). Chaque enregistrement de résultat porte aussi `campaign_sha256`, `manifest_sha256` et
`envelope_sha256` : le lanceur refuse de continuer dans un fichier écrit sous d'autres valeurs. Le registre
est vérifié **au démarrage de la même façon** que les résultats : chacune de ses lignes `session_started`
(y compris celle d'un lancement refusé au préflight, qui n'a laissé aucun résultat) doit porter les mêmes
empreintes de campagne, de manifeste et d'enveloppe que ce lancement, sinon le lanceur refuse (code 2)
**avant** toute réservation, tentative ou ligne écrite : modifier la configuration ou l'enveloppe en gardant
l'identifiant de campagne ne remet pas les plafonds à zéro. Chaque tentative locale est inscrite au
registre (`attempt_started`, avec son nom `attempt_dir`) avant de s'exécuter et réglée (`settled`, même
`attempt_dir`) à sa fin, y compris interrompue.

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
| `session_log` | `{"host": "claude", "projects_dir": "~/.claude/projects", "layout_verified": false}` (pilotes cloud) : seul `<projects_dir>/*/<session>.jsonl` est lu ; tant que `layout_verified` n'est pas `true`, les tokens premium de l'exécution sont inconnus (`log_layout_unverified`) |

Clés de la configuration : `isolation.deny_read_home` (listes `local` et `cloud` d'entrées du HOME réel à
interdire en lecture, relatives, sans `..`) et `non_protocol_choices` (voir plus bas).

Le pilote `local_harness` est la commande `omp` (18.4.10) éprouvée le 2026-10-05 ; le harnais neutre
et les trois pilotes cloud (Claude Code en mode non interactif) sont déclarés `"verified": false`.

## Mesures et résultats

Un enregistrement par (tâche, parcours, tentative) dans `<state-dir>/results-<campagne>.jsonl`
(`foundry.local-first-result.v1`) : `task` (`pr`, `issue`, `set`), `path` (`S` pour le tamis, `A`, `B`,
`C`, `N`), `segment` (`local`, `cloud`, `takeover`), `attempt`, `outcome`, `judge` (verdict et comptes),
`accepted` (vrai seulement quand le parcours se termine accepté : tests protégés verts **et** revue sans
blocage ; nul sinon), `review` (`rounds`, `verdicts`), `wall_seconds`, `cloud_executions`,
`cloud_sessions` (l'identifiant de session de chaque exécution cloud inscrite au registre pendant cette
tentative), `premium`
(`by_role` par classe de token, `billing_total`), `local` (harnais, candidat, `steps`, `stream_tokens`,
vitesses, `timed_out`, `step_limit_hit`, code et signal de sortie, `ended_by_external_signal`),
`machine` (`before`/`after` : `swap_used_mib`, `pressure_free_percent`, `server_rss_kib`), `unknown`
(raison de chaque donnée absente). Une donnée absente est `null` avec sa raison, jamais 0.

- **Provenance** : `dry_run`, `campaign_sha256` (sha256 du fichier de campagne), `manifest_sha256`,
  `envelope_sha256` sur chaque enregistrement ; `report` les vérifie (une seule valeur par fichier, la
  configuration passée à `--campaign` doit avoir le sha256 des résultats), refuse les tentatives en double
  et les fichiers mêlant essai à blanc et réel, puis imprime `rules_applied` et `provenance`.
- **Tentative interrompue** : sur Ctrl-C, SIGTERM, SIGHUP ou toute autre exception **à n'importe quel moment
  de `screen` et de `compare`** (construction du bundle, patch, juge, lecture des journaux, écriture du
  registre ou d'un enregistrement, nettoyage d'un pilote), le lanceur écrit un enregistrement
  `status: interrupted` (`outcome: interrupted`, `reason`, `premium.billing_total` **nul**, les
  `cloud_sessions` déjà inscrites), puis un enregistrement `stop` (`interrupted:<exception>`), puis relaie
  l'exception. Une tentative locale interrompue est réglée au registre (sa durée compte) et sa clé est
  prise : elle ne se rejoue pas.
  **Couvert exactement** : SIGTERM, SIGHUP et Ctrl-C (SIGINT) reçus par le processus du lanceur pendant
  `screen` ou `compare`, hors des quelques instructions qui installent ou rétablissent les gestionnaires.
  Ils sont convertis en `SystemExit(128 + n)` / `KeyboardInterrupt` ; le groupe de processus d'un pilote en
  cours est tué ; un signal reçu **pendant** une écriture au registre, une écriture de résultat, un
  règlement ou le nettoyage d'un pilote est noté et levé juste après (l'écriture n'est jamais coupée en
  deux) ; un second signal n'est jamais avalé (il remplace le premier une fois l'enregistrement et
  l'arrêt écrits) ; les gestionnaires précédents sont rétablis à l'identique en sortie. Une tentative
  locale réglée mais dont l'enregistrement n'était pas encore écrit (signal entre le retour de la tentative
  et son écriture) est écrite `interrupted` avant le `stop`. Un signal qui tombe pendant l'écriture même de
  l'enregistrement fini laisse **cet** enregistrement (complet, une seule fois), puis le `stop`.
  **Non couvert** : SIGKILL, coupure de courant, plantage de l'interpréteur ou de la machine, un signal
  reçu avant `screen`/`compare` (construction du lanceur) ou hors du fil principal (aucun gestionnaire
  n'y est installé), et un SIGINT ignoré au départ (il le reste). Dans ces cas rien ne peut être écrit :
  une tentative **locale** tuée ne laisse ni règlement ni enregistrement (sa durée n'est pas comptée et
  elle reste rejouable dans le même état) ; c'est le registre qui le dit à `report` (voir Rapport :
  `ledger.unsettled_starts`, avertissements, décision `inconclusive`). L'opérateur ne relance pas un
  état interrompu.
- **Total premium d'un enregistrement** : connu seulement si les compteurs de **chaque** exécution cloud
  de `cloud_sessions` ont été lus ; une exécution inscrite au registre dont le coût n'a pas été relu rend
  le total nul (inconnu), jamais 0.
- **Arrêt sur plafond** : le tour coupé (y compris une correction ou une reprise qui n'a pas pu démarrer)
  reçoit un enregistrement `stopped_by_cap` : la tâche n'est pas décidée, ce n'est pas un refus.
- **Panne d'outil** : une `CorpusError`/`RunnerError` n'est jamais un verdict. Elle écrit un
  enregistrement `status: tool_error` (avec `reason`, et le coût déjà engagé) avant l'arrêt
  (`stopped`, raison `tool_error:…`, code de sortie 2). Une revue illisible est `review_unreadable` :
  `accepted` nul et qualité `unavailable` (pas `fail`). Une tentative locale tronquée par le budget de
  durée restant consigne la borne **effective** (`local.max_seconds`) et la borne nominale
  (`local.nominal_max_seconds`).
- **Tokens premium** : chaque exécution cloud reçoit un identifiant de session généré par le lanceur
  (`--session-id`) ; le journal de session de l'hôte est retrouvé par cet identifiant (jamais par
  fenêtre de temps) et lu par `cost_attribution.read_host_log`. Journal absent, ambigu, illisible, d'une
  autre session ou de disposition non vérifiée : tous les compteurs de l'exécution sont inconnus. Le
  détail par modèle (`premium.by_model`) est consigné et repris par le rapport. La classe de raisonnement que l'hôte ne
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
comparé (`pass`, `fail` ou `unavailable`) et ne promeut jamais (`"promotion": false`).

**Registre et résultats ne se contredisent jamais sur le travail dépensé.** `report` croise les deux
fichiers par identifiant de session. Invariant vérifié : chaque `cloud_started` du registre est nommé par
**exactement un** enregistrement de résultat (`cloud_sessions`) et réglé avec des tokens connus ; chaque
exécution cloud des résultats a sa ligne au registre.

- Refus (code 2), les deux fichiers ne décrivent pas la même campagne : registre absent ou vide, registre
  d'une autre nature (à blanc/réel) ou écrit sous d'autres empreintes (enveloppe, configuration,
  manifeste), enregistrement qui ne nomme pas ses exécutions cloud, session inconnue du registre, session
  nommée deux fois, total premium d'un enregistrement que le registre ne confirme pas.
- **Départs non réglés** (`ledger.unsettled_starts`) : `report` liste aussi chaque `attempt_started` du
  registre sans `settled` de même `attempt_dir` (tentative locale tuée, éventuellement rejouée par un
  lancement ultérieur) et chaque préflight réussi suivi d'aucun démarrage de tentative avant le préflight
  ou le lancement suivant (un préflight suivi d'un `stopped` propre n'en est pas un). Chaque entrée porte
  le mode de la session qui l'a écrite et un `warning` nommant la tentative ou le préflight. Pour le tamis,
  ces avertissements figurent dans `screening.warnings` : `selected` reste (le résultat des enregistrements
  est intact) mais le tamis n'est pas présenté comme propre. Pour la comparaison, ils figurent dans
  `comparison.warnings` et la décision est `inconclusive` (jamais `retained` ni `keep_cloud`).
- **Candidat comparé** : `report` refuse (code 2) une comparaison dont le candidat local (parcours `C` ou
  `N`) n'est pas celui que le tamis a désigné par la règle préenregistrée (`selected`) ; un tamis incomplet
  ou sans vainqueur ne désigne personne, donc refuse aussi. `compare` fait la même vérification au
  démarrage, avant toute réservation, quand les résultats du tamis sont dans le même dossier d'état
  (seulement pour les parcours `C`/`N` ; `A` et `B` n'ont pas de candidat local). Sans résultats de tamis
  dans les fichiers, le contrôle est impossible : `comparison.screening_selected` vaut
  `screening_results_not_available` (rien n'est supposé).
- Travail dépensé inconnu (`ledger.unknown_spent_work` liste les raisons) : `cloud_started` sans `settled`
  apparié, `settled` interrompu, `settled` aux tokens inconnus, session du registre sans enregistrement de
  résultat. Dans ces cas **aucun** verdict d'économie n'est rendu (`unavailable` pour tous les parcours :
  le registre ne sait pas à quel parcours attribuer le travail perdu) et la décision est `inconclusive`,
  jamais `retained` ni `keep_cloud`. Un enregistrement `interrupted` donne la même décision
  `inconclusive`, même quand le registre est complet (interruption après le règlement) ; l'économie du
  parcours touché est alors `unavailable` par son total premium nul.

Règles :

- **tamis** : le candidat qui fait accepter le plus de tâches ; à égalité la durée totale la plus courte ;
  sous `min_accepted` (2) aucun candidat, « conserver le cloud » ; égalité de durée : non résolue ;
  `selected` reste nul (`incomplete_screening`) tant que chaque candidat n'a pas un enregistrement pour
  chaque tâche du tamis (une panne d'outil n'est pas un refus : elle rend le tamis incomplet) ;
- **compatibilité** (C) : aucune tentative interrompue sur un signal extérieur et swap supplémentaire sous
  `extra_swap_gib_max` (10 Go) ; swap non mesuré : `unavailable` ;
- **qualité** : toutes les tâches acceptées et pas plus de tours de revue au total que A ; une tâche
  dont le verdict est inconnu (revue illisible, panne d'outil, interruption, arrêt sur plafond) rend la
  qualité `unavailable`, pas `fail` ;
- **économie** : travail premium total inférieur d'au moins 25 % à A **et** durée totale au plus 2 fois A ;
  `unavailable` si le travail premium d'un des deux parcours n'est pas mesurable, si le registre connaît
  du travail dépensé inconnu, ou si une tâche comparée (du parcours ou de A) n'est pas décidée : un
  parcours coupé avant son terme n'a pas de coût complet, le comparer donnerait un chiffre trop
  favorable. **Cette règle « indisponible dès qu'une tâche comparée n'est pas décidée » est un choix du
  lanceur, pas une coordonnée du protocole** (le protocole ne dit rien d'une tâche non décidée) : elle est
  étiquetée comme telle dans `non_protocol_choices`
  (`economy_unavailable_when_a_compared_task_is_undecided`). Le travail premium est
  la somme **non pondérée** des quatre classes de tokens facturables sur tous les modèles : c'est une
  limite (un token d'un modèle économique pèse autant qu'un token premium), d'où le détail par modèle
  (`economy_detail.premium_by_model`) nécessaire pour juger honnêtement le parcours B ;
- décision : `retained` (recommandation B si B suffit, sinon C) seulement si les trois verdicts passent
  sur le nombre de tâches prévu ; `keep_cloud` sur un échec ou sur un arrêt anticipé **de règle**
  (`fewer_than_min_local_successes`, `premium_c_not_below_a`) ; un arrêt sur plafond, sur panne
  d'outil ou sur interruption laisse une campagne incomplète : `inconclusive` (un `fail` réellement
  mesuré sur une autre tâche reste un `fail`, sauf interruption ou travail dépensé inconnu, qui donnent
  toujours `inconclusive`).

Le rapport ne dit pas qu'un échantillon de 6 tâches est une preuve statistique générale : il ne l'est pas.

## Isolement du candidat (pendant sa tentative)

Appliqué à chaque pilote lancé par le lanceur (`execute_driver`) :

- **Dépôt** : bundle PAT-107 (un commit racine, sans lien avec le dépôt de développement), hors du
  checkout ; ni les tests protégés, ni le SHA fusionné, ni les seuils n'y figurent (testé). Le pied
  d'énoncé est commité dans la base : `git diff HEAD` du relecteur ne montre que le travail du bras.
- **Bac à sable macOS** (`sandbox-exec`, profil généré par `sandbox_profile` : `(allow default)` puis
  refus) : écriture interdite hors du bundle et du dossier d'essai (plus `extra_write` pour un pilote
  cloud) ; **lecture interdite** (liste explicite, `read_deny_list`) de : chaque checkout et worktree de ce
  dépôt que `git worktree list` énumère, le dossier d'état, le dossier parent de chacun des fichiers
  `--envelope`, `--campaign`, `--snapshot`, `--manifest` (à garder à part de `--work-root`, sinon le
  lanceur refuse de générer le profil), et dans le HOME **réel** les entrées de
  `isolation.deny_read_home` : pour un pilote local `.claude` (dont le cache de plugins qui contient les
  tests fusionnés), `.claude.json`, `.codex`, `.config`, `.ssh`, `.gnupg`, `.aws`, `.netrc`,
  `Library/Keychains`, `.git-credentials`, `.npmrc`, `.pypirc`, `.docker`, `.kube`, `.zsh_history`,
  `.bash_history`, `.python_history` ; pour un pilote cloud la même liste sans `.claude`, `.claude.json` ni
  `.config` entier (identité OAuth, AGENTS.md R6 ; Claude Code lit et écrit `.claude.json`), avec
  `.config/foundry`. Le profil généré, qui nomme tous les chemins refusés, est écrit dans un dossier
  temporaire lui-même interdit en lecture au candidat (sonde testée). Réseau limité à la boucle locale pour
  un pilote local. Un vrai lancement exige `sandbox-exec` (refusé ailleurs).
  **Trousseau non couvert** : `(allow default)` laisse l'accès Mach au démon de sécurité ; refuser la
  lecture de `Library/Keychains` n'empêche donc **pas** une requête au trousseau (`security
  find-generic-password`, API Keychain). Rien n'est imposé là-dessus ici : voir les préconditions PAT-109.
  Ce n'est **pas** un refus par défaut du HOME avec liste d'autorisation (outil, bundle, dossier d'essai,
  installation de l'interpréteur) : cette forme, préférable, n'est pas vérifiable sans lancer `omp`, ce que
  ce ticket n'a pas le droit de faire ; la liste de refus est une coordonnée de configuration
  (`isolation.deny_read_home`) que PAT-109 doit remplacer par la liste d'autorisation après test de fumée.
- **Environnement** : liste blanche (`PATH`, `LANG`, `LC_ALL`), HOME et XDG isolés dans le dossier
  d'essai, aucune variable `FOUNDRY_*`, aucun jeton, aucun agent SSH ; un pilote cloud garde le vrai
  HOME (identité OAuth de Claude Code, AGENTS.md R6) et ne reçoit que ce que `env_allow` nomme ;
  entrée standard fermée.
- **Interruption et durée** : le groupe de processus du bras est tué à la borne de durée ou d'étapes, et
  aussi sur Ctrl-C, SIGTERM, SIGHUP ou toute exception du lanceur (`try/finally` ; SIGTERM et SIGHUP sont
  convertis en `SystemExit` pour toute la durée de `screen` et de `compare`, pas seulement pendant
  l'exécution d'un pilote). Un signal reçu pendant le démarrage du bras (`Popen`)
  est différé jusqu'à ce que le processus soit connu, puis le tue ; un signal reçu pendant le nettoyage
  (profil temporaire, rétablissement des gestionnaires) est levé une fois le nettoyage terminé ; les
  gestionnaires de signaux précédents sont rétablis à l'identique en sortie. Une exécution cloud interrompue est réglée dans le
  registre avec des tokens inconnus. **Non imposé** : un enfant qui appelle `setsid` sort du groupe et
  survit au `kill` ; seule parade, un contrôle de quiétude avant le jugement : le lanceur compare deux
  empreintes (chemin, taille, mtime) du bundle à 0,2 s d'écart et refuse de juger (`tool_error`) si
  elles diffèrent. Un enfant échappé qui ne fait rien ne se voit pas.
- **Git sur un bundle** : toute commande git du lanceur sur un bundle neutralise la configuration
  (`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` vers `/dev/null`, `core.fsmonitor=false`,
  `core.hooksPath=/dev/null`, `protocol.file.allow=never`, `core.attributesFile=/dev/null`, `--no-ext-diff`,
  `--no-textconv`) et refuse un bundle dont `.git` n'est plus un dossier simple, dont `.git/config`,
  `.git/info/attributes` ou un `.gitattributes` a changé depuis le commit racine. Le patch est pris contre
  le SHA du commit racine enregistré à la construction (un bras qui commite donne son patch) et les
  fichiers nouveaux y figurent ; il est appliqué avec `--index` pour la revue.
- **Juge** (`local_first_corpus`) : l'état « jugé » est tenu par le lanceur (clé : chemin du bundle),
  jamais dans l'arbre du candidat ; un lien symbolique sous les préfixes de code produit (ou l'un de leurs
  parents) donne un `REFUSED` (`symlink_in_product_source`).
- **Sondes de confinement** testées sous `sandbox-exec` : écriture hors dossier, lecture des seuils,
  lecture d'un fichier canari sous un faux `~/.claude/plugins`, `~/.ssh`, `~/.config`, `~/.aws` et dans le
  dossier de l'enveloppe, connexion non bouclée refusées par `EPERM`, boucle locale permise, variable
  canari absente. Là où `sandbox-exec` est indisponible (CI Linux) ou déjà confiné, les sondes sont
  **ignorées** avec leur raison (`pytest -rs`) ; le profil généré, la liste de refus et la liste blanche
  d'environnement restent testés. Chaque exécution de la suite indique donc explicitement « passées » ou
  « ignorées » : à reporter dans la PR pour la machine qui a fait tourner les tests.

Ce que l'isolement **n'impose pas** : le candidat lit le reste du disque (liste de refus, pas liste
d'autorisation : tout chemin non nommé est lisible, par exemple une copie des tests fusionnés ailleurs que
dans le cache de plugins), un pilote cloud a le réseau ouvert, l'écriture dans `~/.claude` et la lecture de
`~/.claude` (donc du cache de plugins et des tests fusionnés : biais possible sur les bras cloud, à
traiter par PAT-109), le binaire du harnais et sa configuration réelle ne sont pas vérifiés ici (PAT-109),
un enfant sorti du groupe par `setsid`, et le code produit reste importé dans le processus de test (limite
du juge). Le suivi du tracker et des secrets repose sur la liste blanche d'environnement et sur les
refus de lecture ci-dessus, pas sur une preuve d'absence.

## Confiance dans les fichiers d'état

Le registre et le fichier de résultats sont des fichiers **de l'opérateur, sans protection d'intégrité**
(ni signature, ni chaînage) : supprimer une ligne de résultats permet de rejouer une tentative, supprimer
une ligne du registre remet un plafond à zéro, et le croisement de `report` ne détecte que les
incohérences entre les deux fichiers, pas une suppression cohérente dans les deux. Le candidat, lui, ne
peut ni les lire ni les écrire (dossier d'état interdit en lecture, écriture limitée au bundle et au
dossier d'essai). L'honnêteté de la campagne repose donc sur l'opérateur : un seul dossier d'état par
identifiant de campagne, jamais d'édition à la main. Une enveloppe renouvelée porte un **nouvel
identifiant de campagne**, donc un registre et un fichier de résultats distincts ; les résultats de deux
enveloppes ne se mélangent pas (empreinte `envelope_sha256` vérifiée).

## Choix du lanceur qui ne sont pas des coordonnées du protocole

Étiquetés `not_a_protocol_coordinate: true` avec leur raison dans `non_protocol_choices` de la
configuration de campagne : `min_free_disk_gib` (30, marge de sécurité) ; le temps compté dans le verdict
d'économie (`time_ratio_max`) ; le swap supplémentaire mesuré comme le maximum, sur les tentatives locales,
de (après − avant) ; le « travail premium » défini comme la somme non pondérée des quatre classes de
tokens sur tous les modèles (limite : voir Rapport) ; l'économie « indisponible » dès qu'une tâche comparée
n'est pas décidée (voir Rapport). `bounds.cloud_max_seconds`, `max_correction_rounds`,
`isolation` et le pied d'énoncé sont aussi des choix du lanceur. Ils ne sont pas gelés par le protocole.

## Préconditions pour PAT-109

Avant toute exécution réelle : tester et épingler chaque pilote non vérifié ; **vérifier la disposition des
journaux de session** de l'hôte cloud (les transcriptions de sous-agents sont-elles dans d'autres
fichiers ? tant que ce n'est pas établi, `session_log.layout_verified` reste `false` et les tokens premium
sont inconnus, donc aucune campagne cloud plafonnée ne peut avancer au-delà de la première exécution) ;
remplacer la liste de refus de lecture par une liste d'autorisation après test de fumée de `omp` ;
décider du biais des bras cloud qui lisent `~/.claude` ; consigner les empreintes des poids ;
**trouver où le jeton du tracker est stocké** (fichier, trousseau, variable) et, s'il est au trousseau,
refuser ce service dans le profil, par exemple `(deny mach-lookup (global-name
"com.apple.SecurityServer") …)`, à condition que le harnais fonctionne encore ainsi (à vérifier par test
de fumée : non essayé ici).

## Statut documentaire (AGENTS.md R5)

Artefacts documentés ici : la surface CLI de `foundry.local_first_runner` (dont l'absence de reprise et
le refus d'une configuration différente à `report`), la configuration de campagne
(`pat-19-campaign-v1.json` : `isolation`, `non_protocol_choices`, `session_log.layout_verified`), le format
d'enveloppe et de registre (`dry_run`, empreintes, `settled` apparié), le schéma des résultats (`tool_error`,
`interrupted`, `stopped_by_cap`, `cloud_sessions`, `premium.by_model`), les règles du rapport (registre
obligatoire, invariant registre/résultats), la confiance accordée aux fichiers d'état et le périmètre de
l'isolement. Aucune constante publique, option de `foundry_cli.py`, clé de
configuration produit ni table de routage n'a changé. L'application mécanique de R5 reste celle de
FOUNDRY-123.
