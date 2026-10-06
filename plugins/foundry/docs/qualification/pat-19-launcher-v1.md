# PAT-19 — Lanceur de comparaison, version 1

PAT-108. Cadre : PAT-ADR-0015 (coût net par tâche acceptée, trois verdicts séparés, aucune
promotion), FOUNDRY-ADR-0019 (comparaison bornée, règle fixée d'avance, arrêt séquentiel, pas de
matrice ni de second cadre), FOUNDRY-ADR-0010 (enveloppe d'autorisation vérifiée avant toute
frontière hôte), FOUNDRY-ADR-0015 (coût lu dans les journaux de session de l'hôte : une donnée
inconnue reste inconnue, jamais zéro), FOUNDRY-ADR-0007 (aucun rôle local dans le produit). Protocole :
[`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) ; corpus et juge : [`pat-19-corpus-v1.md`](pat-19-corpus-v1.md) ; résultats du premier essai réel (tamis, 2026-10-06) : [`pat-19-screening-results-v1.md`](pat-19-screening-results-v1.md) ; décision qui en découle (PAT-110) : [`pat-19-decision-v1.md`](pat-19-decision-v1.md).

C'est un outil de campagne : il ne touche ni routage, ni mappings, ni rôles, ni valeurs par défaut,
ne charge aucun modèle (aucune commande `lms load`), n'appelle ni tracker ni réseau, et ne promeut
rien. Ce ticket ne l'exerce qu'avec des parcours **factices** (mode à blanc) ; les exécutions réelles
sont celles de PAT-109, avec la confirmation du mainteneur.

**Épinglage des pilotes (PAT-111).** Les cinq pilotes réels et les cinq candidats locaux ont été essayés pour de
vrai le 2026-10-05, sur une tâche jouet (hors corpus), par le coordinateur, avec la confirmation du
mainteneur pour chaque chargement de modèle et chaque appel cloud ; les preuves sont versées dans
[`pat-19-preflight-2026-10-05.json`](pat-19-preflight-2026-10-05.json) (sans secret ni chemin absolu) et
chaque pilote `verified: true` y renvoie par sa clé `evidence`. Voir « Pilotes épinglés » plus bas. Le lanceur
lui-même n'a lancé aucun modèle ni appel cloud pour ce ticket.

Le protocole a été amendé sur place avant tout essai (candidat 2, trois téléchargements) ; le mainteneur
l'a validé le 2026-10-05. La note datée en tête de [`pat-19-protocol-v1.md`](pat-19-protocol-v1.md) en
fait l'historique ; aucun essai n'avait eu lieu et aucun fichier v2 n'existait alors (le protocole v2, PAT-114, est décrit dans la section « Protocole v2 » en fin de ce fichier et dans [`pat-19-protocol-v2.md`](pat-19-protocol-v2.md) ; la v1 reste gelée).

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
    [--screening-campaign <id>] [--harness ...] [--dry-run] [--sandbox]
python3 -m foundry.local_first_runner report --campaign <cfg> --results <results-<campagne>.jsonl>
```

Codes de sortie : 0 terminé, 2 refus ou erreur d'outil (pas d'enveloppe, enveloppe invalide, préflight
refusé (dont contexte chargé trop petit, quantification ou clé de modèle différente), pilote non vérifié,
exécutable de harnais introuvable ou en une autre version, enregistrement cloud refusé faute de preuve d'absence
d'outil de sous-agent, dossier de travail dans le checkout, tentative déjà consignée, état mêlant
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
- **Reprise bornée** (règle du mainteneur, 2026-10-05) : une relance de `screen` ou de `compare` sous le
  **même** identifiant de campagne, la même enveloppe, la même configuration et le même manifeste reprend
  la campagne ; sous d'autres empreintes elle est refusée comme avant. Le tamis se fait candidat par
  candidat, en plusieurs lancements. Définitions telles que codées :
  - **Décidée** : tentative (locale ou tour cloud) dont un enregistrement porte un verdict du juge (accepté
    ou refusé, `judge` non nul), y compris un enregistrement `interrupted` ou `tool_error` **écrit après le
    verdict ou après la revue** : une coupure entre le verdict (ou la revue) et l'écriture de
    l'enregistrement final garde le verdict (et la revue) reçus sur l'enregistrement coupé, qui n'est donc
    jamais rejoué (pas de seconde chance après un verdict ; testé pour le local, pour le cloud juste après le
    juge, pendant le nettoyage de la revue, au retour de la revue et juste avant l'écriture) ; pour le cloud,
    un parcours terminé (`accepted`, `review_unreadable`, ou dernier tour `review_block`/`judge_refused`).
    Une tentative décidée est **sautée, jamais rejouée**, quoi qu'il arrive ensuite (un seul enregistrement
    par tentative décidée : `report` refuse un doublon) ; le préflight n'est pas refait pour une tâche
    sautée. Un enregistrement coupé après verdict reste `interrupted` (inconnu, jamais accepté, coût
    conservé) : la tâche est indécise, pas rejouée. Reste une fenêtre de quelques instructions entre le
    retour du juge et l'affectation de son résultat (`verdict = lfc.judge(...)`) qu'un signal peut encore
    couper : elle n'est pas fermée (la fermer demanderait de différer les signaux pendant toute la durée du
    juge).
  - **Nulle** (*void*) : coupée **avant** tout verdict : enregistrement `interrupted` ou `tool_error` sans
    `judge`, ou `attempt_started` au registre sans `settled` ni enregistrement (tuée). Seules les pannes du
    lanceur ou de l'environnement **avant** que le bras ne tourne (construction du bundle, démarrage du
    pilote), ou hors du bundle ensuite, sont nulles. Une panne sur le bundle **après** l'exécution du bras est
    **causée par le candidat** et vaut **refus** (verdict `REFUSED`, note `candidate_fault: …` avec l'erreur,
    coût conservé), décidé, jamais rejoué : `.git` n'est plus un dossier simple, sa configuration ou ses
    attributs ont changé, l'arbre bouge encore (contrôle de quiétude), **toute commande git qui prend le patch
    échoue** (`.git/index.lock` laissé par le bras tué à la borne, dépôt imbriqué vide d'un `git init sub/`,
    fichier illisible…), ou le juge reçoit une `OSError` sur un fichier **du bundle** (lecture de
    `_candidate_changes`, suppression de bytecode de `purge_bytecode`). Une `OSError` du juge sur un chemin
    hors du bundle reste nulle. En cloud le tour est jugé refusé et une correction peut suivre.
  - **Rejouable** : une tentative locale nulle l'est **une seule fois**, sur un bundle neuf ; le rejeu est
    enregistré avec `replay_of` (`attempt`, `attempt_dir` de la tentative nulle, `outcome`, `reason`
    ; `outcome: hard_kill` pour une tentative tuée). Une seconde coupure de la même tentative (deux
    départs au registre) n'est pas rejouable : la tâche reste **indécise** pour ce candidat. Une tentative
    réglée au registre sans enregistrement (tuée entre les deux écritures) peut avoir reçu un verdict : elle
    n'est jamais rejouée non plus.
  - **Cloud** : rien ne change pour l'argent : toute exécution déjà réservée reste comptée au registre et
    dans le coût du parcours (un tour nul n'est pas remboursé ; son coût, connu ou inconnu, reste attaché à
    la tâche). Seul un **premier** tour (indice 0) nul est rejoué (une fois) : le patch et les remarques
    d'un tour ne sont pas conservés, donc une correction ou un relais coupé laisse le parcours indécis. Le
    rejeu passe par les plafonds : une dépense non mesurée (tour interrompu pendant l'exécution) arrête
    toute exécution cloud (`premium_tokens_unmeasurable`), donc ne se rejoue pas. Un tour tué (SIGKILL) après
    son règlement mais avant son enregistrement est rejoué **sans** `replay_of` (rien ne le relie) ; `report`
    le liste dans `void_attempts` (une entrée par session du registre qu'aucun enregistrement ne nomme :
    `outcome: hard_kill`, `reason: cloud_started without a result record`, `role`, `replayed` nul = inconnu).
    Parcours C : une tentative locale décidée n'est jamais rejouée ; si elle a demandé le relais
    (`local_refused`, `review_block`) ou si elle-même a été contaminée et que le relais manque, il est joué ;
    une revue coupée laisse le parcours indécis ; une revue **contaminée** laisse la tâche indécise **sans
    relais**, coût conservé, au premier lancement comme à la reprise (même comportement, testé).
  - **Nommage des sessions** (invariant registre/résultats) : l'enregistrement nul nomme ses propres
    `cloud_sessions` (celles qu'il a réservées) et le rejeu les siennes (une liste par lancement) : chaque
    session du registre reste nommée exactement une fois. Le rejeu a la clé de la tentative nulle plus un
    rang de rejeu (1) : la clé complète d'un enregistrement reste unique.
  - Les plafonds de l'enveloppe sont **cumulés** entre lancements (registre) : une reprise ne remet rien à
    zéro et n'élargit rien. Les seuils d'arrêt de `compare` (`min_local_successes`, premium C ≥ A) repartent
    des enregistrements déjà écrits.
  - Un candidat qui garde une tâche indécise après le rejeu permis n'est pas retenu : `screening.selected`
    est nul, `reason` reste `incomplete_screening` et `screening.undecided_tasks` dit quelles tâches.
    `report` liste chaque tentative nulle (`void_attempts`, avec `replayed`) et chaque rejeu (`replays`) et
    compte une tâche une fois (l'enregistrement décidé). Un départ tué dont le rejeu est enregistré n'est
    plus un avertissement ; sans rejeu il reste un avertissement (et `killed_not_replayed` le compte pour le
    tamis). Les règles d'économie et de décision pour le travail dépensé inconnu sont inchangées.
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
`plutil -extract CFBundleShortVersionString raw` sur le `Info.plist` de `/Applications/LM Studio.app` (version de l'app ; `lms version` ne donne que le commit du CLI, un plist absent ou illisible rend le fait indisponible donc refuse), `lms runtime ls`, `lms ps --json`, `ps`. Il refuse quand : une coordonnée gelée de
`frozen_machine` diffère (puce, mémoire, macOS, LM Studio, moteur MLX), un fait est indisponible,
un autre modèle que celui attendu est chargé ou le modèle attendu ne l'est pas (`lms ps` doit lister
exactement l'identifiant), la place disque est sous le minimum. Il relève le swap et la pression
mémoire de départ. `screen` et `compare` refusent de lancer (code 2, aucun pilote lancé) si le préflight échoue.
**Instance chargée (PAT-111).** Quand le candidat déclare `min_context`, `quantization` ou `lm_studio_key`
(tous les candidats épinglés le font), le préflight lit l'instance listée par `lms ps --json` et refuse
(codes de refus) si : sa longueur de contexte est inférieure à `min_context` (65 536, coordonnée gelée :
LM Studio a ignoré `-c` pour certains builds MLX et chargé plus, ce qui est accepté ; il ne faut pas
supposer la valeur demandée, d'où `loaded_context_below_minimum`) ou absente (`loaded_context_unknown`) ;
sa quantification diffère (`loaded_quantization_differs`) ou est absente (`loaded_quantization_unknown`) : le
candidat 4 bits dépend de la variante sélectionnée de `qwen/qwen3.8-27b`, que le préflight vérifie ; sa clé
de modèle diffère (`loaded_model_key_differs`). Les champs lus sont `contextLength` (ou `context_length`),
`quantization` (texte ou objet `name`) et `modelKey` ; un champ absent refuse (inconnu n'est jamais un
succès), la clé de modèle comprise (`loaded_model_key_unknown`). **Observé** le 2026-10-05 avec un modèle
chargé (consigné dans `pat-19-preflight-2026-10-05.json#lms_ps_json_observed`) : `modelKey`, `identifier`,
`quantization` (objet `{name, bits}`) et `contextLength`. L'opérateur charge toujours le modèle (`load_command`
du candidat) ; le lanceur ne charge rien.

Les empreintes des poids, l'empreinte du gabarit de conversation et les paramètres de génération de chaque
candidat sont **consignés** dans la configuration (`file_sha256`, `chat_template_sha256`,
`generation_config_sha256`, `generation_parameters` : valeurs par défaut du serveur et du harnais, jamais
surchargées) et dans le fichier de preuve ; le lanceur **ne les recalcule pas** (hacher 17 Go à chaque
préflight n'est pas fait) : le préflight vérifie l'identité de l'instance chargée (clé, quantification,
contexte), pas le contenu des fichiers.

## Configuration de campagne (`pat-19-campaign-v1.json`)

Clés : `frozen_machine`, `server_process_pattern` (expression pour la mémoire du serveur), `bounds`,
`statement_footer`, `prompts` (`implement`, `correct`, `review`), `rules` (`screening`, `comparison`),
`drivers`, `candidates` (identifiant → `model`, et pour un candidat épinglé : `lm_studio_key`, `engine`,
`quantization`, `min_context`, `load_command`, `file_sha256`, `chat_template_sha256`,
`generation_config_sha256`, `generation_parameters`, `smoke`, `hf_repo_provenance`). Un pilote est une donnée :

| Clé | Sens |
| --- | --- |
| `kind` | `local_harness`, `neutral_harness`, `cloud_implementer`, `cloud_reviewer` |
| `argv` | commande ; variables `{workdir}` `{statement_file}` `{model}` `{prompt}` `{max_seconds}` `{max_duration}` `{max_steps}` `{session_id}` `{review_file}` `{feedback_file}` `{scratch}` |
| `verified` | un vrai lancement refuse un pilote non vérifié ; PAT-109 doit le tester et l'épingler |
| `fake` | pilote de test (mode à blanc seulement) |
| `home`, `network` | `isolated`/`real`, `loopback`/`open` ; un pilote local est toujours `isolated` + `loopback` (refusé au chargement sinon) |
| `sandbox`, `sandbox_reason` | `sandbox: false` (pilote **cloud** seulement : refusé pour un pilote local, et sans `sandbox_reason` non vide) : le pilote tourne sans `sandbox-exec` ; absent = confiné |
| `allowed_tools` | liste d'autorisation des outils de l'événement `init` d'un pilote cloud `claude-stream-json` (obligatoire pour un pilote cloud vérifié) : un outil hors liste, ou pas d'événement `init`, refuse l'enregistrement |
| `evidence` | obligatoire pour un pilote `verified: true` non factice : référence `<fichier de preuve>#drivers.<pilote>` |
| `version` | version du harnais ou de l'hôte épinglée (information) |
| `binary_version` | `{command, pattern, version}` (`omp`) : le lanceur exécute `command` (`omp --version`, exécuté hors du bac à sable avec l'environnement de l'hôte, délai de 30 s, avant chaque tentative ; `omp` pris dans le `PATH`), lit la version avec `pattern` (un groupe : `omp/<x.y.z>`) et refuse un exécutable absent, une sortie illisible ou une autre version que `version`, avant toute réservation (début de `screen`/`compare`, et à chaque tentative locale) |
| `executable` | `{env, placeholder, package, version}` : l'exécutable vient d'une variable d'environnement de l'opérateur (chemin absolu, jamais committé) ; le paquet installé dans son environnement virtuel doit avoir la version épinglée (lue dans le `dist-info`, le harnais n'est pas lancé pour la demander) ; `{placeholder}` est substitué dans `argv` |
| `env_set`, `make_dirs` | variables d'environnement fixes (valeurs avec `{scratch}`…) et dossiers créés avant le lancement ; un nom ressemblant à un secret n'est accepté qu'avec un des espaces réservés documentés (`local-endpoint-no-key`), jamais `FOUNDRY_*` |
| `trajectory` | `{file, steps_path}` : fichier de trajectoire lu **après** la fin du processus pour les étapes (harnais neutre) ; la borne d'étapes n'est donc pas imposée pendant l'exécution, seulement la borne de durée |
| `env_allow`, `home_files`, `extra_write` | variables transmises en plus (jamais `FOUNDRY_*`, jeton, clé, secret, agent SSH), fichiers placés dans le HOME isolé, dossiers inscriptibles en plus |
| `stream` | `{"format": "omp-json" \| "claude-stream-json" \| "none", "speed_usage_keys": …}` ; `claude-stream-json` : le lanceur lit l'événement `system/init` (voir « Pilotes épinglés ») |
| `session_log` | `{"host": "claude", "projects_dir": "~/.claude/projects", "layout_verified": false}` (pilotes cloud) : seul `<projects_dir>/*/<session>.jsonl` est lu ; tant que `layout_verified` n'est pas `true`, les tokens premium de l'exécution sont inconnus (`log_layout_unverified`) ; `true` est refusé au chargement sans le flux `claude-stream-json` (l'assertion de la liste d'outils) |

Clés de la configuration : `isolation.deny_read_home` (listes `local` et `cloud` d'entrées du HOME réel à
interdire en lecture, relatives, sans `..`), `isolation.deny_home_by_default` (booléen, vrai par défaut : un
pilote **local** n'a **aucune** lecture du HOME réel, hors liste d'autorisation) et `isolation.allow_read_home`
(entrées du HOME relatives, sans `..`, vide par défaut), `cloud_bash_deny` (règles de permission Bash,
meilleur effort, présentes **à la lettre** dans le `--disallowedTools` de chaque pilote cloud réel : le
chargement refuse sinon) et `non_protocol_choices` (voir plus bas).

## Pilotes épinglés (PAT-111)

**Correction (PAT-112, 2026-10-06)** : le texte disait `omp` 18.4.10, version observée avant la mise à jour. `omp`
installé est 18.6.1 (installé le 2026-10-05 à 17 h 20, heure locale) et chaque essai `omp` consigné (flux de
18 h 07, 18 h 27, 20 h 41, 22 h 53 à 22 h 59) lui est postérieur : la version utilisée est 18.6.1, désormais
vérifiée par le lanceur (`binary_version`).

Un pilote n'est `verified: true` que si son essai réel a réussi (preuve : `pat-19-preflight-2026-10-05.json`) ;
un pilote qui échoue reste `verified: false` et le lanceur le refuse. Les cinq passent le 2026-10-05 :

- **`local_harness`** : `omp` 18.6.1, argv de la configuration, entrée standard fermée, HOME isolé (le
  fournisseur `lm-studio` est découvert depuis le HOME isolé : aucun `home_files`), `--max-time 6m` accepté,
  sous le profil du bac à sable local. Essayé avec les cinq candidats deux fois : d'abord sous le profil à
  liste d'interdits explicite (`smoke_at_65536`), puis le 2026-10-05 sous le profil final qui refuse le HOME
  par défaut (`deny_home_trial` de chaque candidat : chargement confirmé, un seul modèle en mémoire, tests
  jouets verts, audit propre). Les paramètres d'échantillonnage ne sont pas épinglés (valeurs par défaut de
  LM Studio et du harnais, non relevées) : limite du résultat.
- **`neutral_harness`** : mini-swe-agent 2.4.6 (PyPI, environnement virtuel isolé hors du dépôt). L'exécutable
  vient de la variable `PAT19_MINI_BIN` (chemin absolu ; le paquet installé doit être en 2.4.6, sinon le
  lanceur refuse **avant** toute réservation, au début de `screen`/`compare` : `compare` avec le parcours `N`
  vérifie la variable avant le parcours `A`) ; le chemin n'est jamais consigné, seulement le nom de la
  variable, le paquet et la version (`local.harness_executable`). Environnement ajouté : `MSWEA_GLOBAL_CONFIG_DIR`
  (sinon il écrit dans le vrai HOME), `MSWEA_CONFIGURED`, `MSWEA_SILENT_STARTUP`, `MSWEA_COST_TRACKING`,
  `OPENAI_API_BASE`, `OPENAI_API_KEY=local-endpoint-no-key` (espace réservé pour le serveur local, pas un secret)
  et `LITELLM_LOCAL_MODEL_COST_MAP=True` (sans lui LiteLLM tente de télécharger une table de prix : le bac à
  sable le bloque et environ 20 s se perdent). La trajectoire est écrite dans le dossier d'essai ; les étapes
  sont `info.model_stats.api_calls` de la trajectoire, lues après la fin du processus (jamais en cours
  d'exécution) : le lanceur ne peut pas imposer la borne de 40 étapes pendant l'exécution, il la **transmet**
  au harnais (`-c agent.step_limit={max_steps}`, **essayé pour de vrai le 2026-10-05** : 40, `Submitted`, 8 appels
  d'API, tests du jouet réussis) et marque `step_limit_hit` **a posteriori**
  quand `api_calls` dépasse la borne : la tentative est alors **refusée** (jamais acceptée, notée
  `local.step_limit`). Tokens inconnus (aucun flux d'événements). Essai (sans ce drapeau) : réussi, 139 s,
  5 appels d'API.
- **Pilotes cloud** (`cloud_implementer_current` : `claude-sonnet-5-5`, effort `medium` ; `cloud_implementer_economy` :
  `claude-haiku-4-5-20251001`, sans `--effort` (effort nul de Haiku 4.5) ; `cloud_reviewer` : `claude-opus-5-5`,
  effort `high`) : **Claude Code nu** 2.1.285 (pas les définitions d'agent Eiffel ni Maigret : le modèle et
  l'effort du protocole, un prompt identique pour tous les bras), même gabarit d'argv pour tous
  (`--permission-mode bypassPermissions --setting-sources project,local --strict-mcp-config --disallowedTools
  <29 noms d'outils + 19 règles Bash de cloud_bash_deny> --output-format stream-json --verbose`, la liste exacte est
  dans la configuration) ; les textes des prompts sont identiques pour tous les bras et
  rendus par la substitution du lanceur (pas `str.format` : le prompt de revue contient des accolades JSON).
  **L'essai du 2026-10-05 a tourné avec `--disallowedTools Agent` seul** ; les 29 noms ont été observés à
  l'événement `init` seulement (aucun appel de modèle) ; l'argv **final** (les 29 noms et les règles Bash) a ensuite été **essayé pour de vrai le 2026-10-05** (Claude
  Code 2.1.285, tâche jouet, `init` : exactement Bash, Edit, Read, Write) et réussi pour les trois pilotes
  (`pinned_final_argv` de la preuve) : Sonnet 5.5 effort medium 7,6 s (Bash×3), Haiku 4.5 sans effort 13,1 s
  (Read×3, Edit×1, Bash×1), Opus 5.5 effort high 17,1 s (fichier de revue écrit, verdict PASS) ; jetons lus par le
  lanceur égaux au `result.usage` de l'hôte.
  **Sans `sandbox-exec`** (`sandbox: false`, avec sa raison dans la configuration) : sous `sandbox-exec`, Claude
  Code ne peut pas s'authentifier (« Not logged in » trousseau refusé ; « 401 OAuth access token has been revoked »
  trousseau permis, après quoi le mainteneur a dû se reconnecter ; cause non établie). Le bras garde l'environnement
  d'AGENTS.md R6 intact (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR` **réel**, `USER`, rien d'autre),
  son répertoire de travail est le bundle jetable hors du dépôt. Avec `--setting-sources project,local
  --strict-mcp-config`, aucun greffon ni crochet d'utilisateur ne se charge, **donc pas ceux de Foundry : la garde R1
  (`gh pr merge`, `git push` refusés par le crochet `PreToolUse`) ne s'applique pas à ces bras**, et aucun serveur
  MCP. **Non vérifié** : si la mémoire globale `CLAUDE.md` de l'utilisateur est encore chargée
  (asymétrie avec le bras local) : limite du résultat.
  Durées des **premiers essais, avec `--disallowedTools Agent` seul** (pas l'argv final) : 11,6 s / 7 tours
  (Sonnet), 16,3 s (Haiku), 16,5 s (Opus, a écrit le fichier de revue demandé) ; celles de l'**argv final
  épinglé** sont 7,6 s / 13,1 s / 17,1 s (ci-dessus).

**Disposition des journaux de session** (vérifiée sur un essai réel pour les trois pilotes cloud) : les
compteurs que lit `premium_tokens(session_id)` dans `<projects>/*/<session>.jsonl` sont **égaux** à ceux que l'hôte
rapporte (`result.usage`) pour les trois exécutions (Sonnet : entrée 8, cache lu 60 608, cache écrit 7 734, sortie
1 127 ; Haiku : 42 / 102 294 / 9 500 / 1 384 ; Opus : 8 / 51 654 / 18 407 / 1 216) ; un seul fichier portait
l'identifiant de session à chaque fois. `layout_verified: true` est donc posé, **sous la condition** que le lanceur
vérifie à chaque exécution cloud, depuis l'événement `system/init` du flux (`StreamStats.init_tools`), que les outils
de l'hôte restent dans la liste d'autorisation : aucun outil
hors de `allowed_tools` (`Bash`, `Edit`, `Read`, `Write`) n'est disponible : sinon l'enregistrement est **refusé** (`tool_error` après le
règlement du registre : l'argent dépensé reste compté, le total premium de l'enregistrement est inconnu, la campagne
s'arrête). Un événement `init` absent refuse aussi, et un outil de plus (par exemple ajouté par une nouvelle version
de l'hôte) aussi : c'est une **liste d'autorisation** (les outils de l'événement `init` doivent en être un
sous-ensemble), pas une liste d'interdits. **Observé en vrai** (Claude Code 2.1.285, arrêt à l'événement `init`,
aucun appel de modèle) : avec `--disallowedTools Agent` seul, l'événement listait 25 outils dont `Task`, `Workflow`,
`SendMessage`, `Monitor`, `Cron*`, `WebFetch`, `WebSearch`, `Skill`, `RemoteTrigger`, `PushNotification`,
`ToolSearch` (le drapeau ne retirait pas `Task`) ; interdire `ToolSearch` fait apparaître des outils différés
(`Artifact*`, `SendFeedback`, `Task*`) ; avec les 29 noms de la configuration, les outils sont exactement
`Bash`, `Edit`, `Read`, `Write`. **Seul le jeu d'outils intégré est donc restreint à Read, Edit, Write et
Bash** (pas d'outil web, de sous-agent, de workflow ni de planification) ; **Bash lui-même n'est pas restreint**,
sauf par les règles `cloud_bash_deny` (voir « Exposition des bras cloud ») : le réseau et le HOME restent ouverts.
Le harnais local `omp` expose son propre jeu d'outils (read, edit, write, bash, glob
vus dans les essais) : cette asymétrie est consignée, pas égalisée. Le coût en dollars que l'hôte
rapporte (0,054 / 0,036 / 0,182 USD) est informatif, jamais un chiffre d'économie.

**Candidats locaux** (`candidates` ; l'opérateur charge, le lanceur ne charge rien ; commande de chargement :
`lms load <clé> -c 65536 --identifier <clé> --parallel 1`, l'identifiant servi est la clé, qui est aussi le nom
de modèle transmis au harnais ; le dépôt HF du protocole est gardé en `hf_repo_provenance`) :

| Candidat | Clé LM Studio | Moteur | Quant. | Essai à 65 536 |
| --- | --- | --- | --- | --- |
| `qwen3.8-27b-mlx-6bit` | `qwen3.8-27b-mlx-alias` | mlx | 6bit | réussi, 104 s |
| `qwen3.8-27b-mlx-4bit` | `qwen/qwen3.8-27b` (la variante sélectionnée doit être 4bit) | mlx | 4bit | réussi, 63 s |
| `qwen3.6-35b-a3b-mlx-4bit` | `qwen/qwen3.6-35b-a3b` | mlx | 4bit | réussi, 29 s |
| `muse-glimmer-30b-gguf` | `muse-glimmer-30b` | llama.cpp | Q4_K_M | réussi, 91 s (**échoue** au contexte par défaut de 8 192 : boucles de lectures, 361 s) |
| `qwen3-coder-30b-a3b-mlx-4bit` | `qwen3-coder-30b-a3b-instruct-mlx` | mlx | 4bit | réussi, 42 s (**échoue** à 8 192 : 107 lectures, 225 s) |

Le 6 bits n'est atteint que par la clé `qwen3.8-27b-mlx-alias` : un dossier de liens physiques
(`pat19-campaign/Qwen3.8-27B-MLX-6bit-alias`) vers les fichiers du 6 bits, parce que LM Studio regroupe les deux
variantes sous `qwen/qwen3.8-27b` et qu'aucune clé de la CLI ni de l'API ne sélectionne le 6 bits. **Le contexte
de 65 536 est une coordonnée gelée pour chaque candidat** : deux candidats échouent à 8 192, LM Studio a respecté
`-c` pour le GGUF et pour le build MLX de Coder et l'a ignoré pour les builds MLX Qwen3.8 et Qwen3.6 (chargés à
169 728 pour le 6 bits, 208 384 pour `qwen/qwen3.8-27b` 4 bits et 262 144 pour `qwen/qwen3.6-35b-a3b`, relevés le 2026-10-05) : d'où la lecture au préflight. Devstral reste déclaré comme repli de Muse mais
**n'est pas utilisé** (Muse a réussi ; `unused: true`, jamais chargé). Aucun candidat n'a été retiré ni remplacé :
les deux échecs à 8 192 sont dus au contexte par défaut, pas au modèle. Un essai de 8 appels d'outil ne mesure
aucune qualité.

**Biais des bras cloud** (AC7) : un bras cloud sans `sandbox-exec` peut lire tout ce que l'utilisateur peut lire,
y compris le cache de plugins dont les tests ont été fusionnés avant la version 1.0.0 (la plupart des tâches du
corpus). Il est **détecté par un audit après exécution** et consigné comme limite du résultat (voir « Audit de
contamination »), **pas empêché**. Pour les bras **locaux**, le refus par défaut du HOME est **implémenté**
(`isolation.deny_home_by_default`, voir « Isolement du candidat ») et testé sur le profil généré et sous un vrai
`sandbox-exec` avec un faux HOME ; il a été **essayé le 2026-10-05 avec les vrais harnais locaux** (Qwen3.8-27B MLX 4-bit,
`allow_read_home` vide) : `omp` 18.6.1 réussi (45,6 s, tests du jouet réussis) et mini-swe-agent 2.4.6 réussi
(`Submitted`, 8 appels d'API). Le refus par défaut est donc confirmé pour les deux harnais.

## Exposition des bras cloud (ce que le lanceur n'empêche pas)

Les trois pilotes cloud lancent **Claude Code nu** avec `bypassPermissions`, l'outil Bash, le **vrai HOME**, un
**réseau ouvert**, les **identifiants implicites de l'utilisateur** (assistant d'identifiants git via le
trousseau, configuration de `gh`, `~/.config/foundry`) et **sans les crochets de Foundry** (`--setting-sources
project,local` : pas de garde R1 sur `gh pr merge` / `git push`). **Aucun refus de lecture ne s'applique à un
bras cloud** (ni `deny_home_by_default`, ni la liste `cloud` de `deny_read_home`, gardée comme donnée). **Le
tracker, les secrets et la fusion ne sont donc PAS protégés par le lanceur pour les bras cloud.** Un bras cloud
pourrait lire des solutions fusionnées (`gh pr view`, `git clone`, `curl`), agir sur le vrai dépôt par `gh`,
démarrer un autre `claude -p` dont les tokens ne sont jamais lus, ou exfiltrer.

Ce qui s'interpose, **au mieux** :

- **Règles de permission Bash** (`cloud_bash_deny`, données de la configuration, présentes à la lettre dans le
  `--disallowedTools` des trois pilotes ; le chargement refuse un pilote cloud réel auquel il en manque une) :
  `Bash(gh:*)`, `Bash(git push:*)`, `Bash(git remote:*)`, `Bash(git clone:*)`, `Bash(git fetch:*)`,
  `Bash(git pull:*)`, `Bash(curl:*)`, `Bash(wget:*)`, `Bash(claude:*)`, `Bash(codex:*)`, `Bash(omp:*)`,
  `Bash(ssh:*)`, `Bash(scp:*)`, `Bash(nc:*)`, `Bash(python3 -m http:*)`, `Bash(pip install:*)`,
  `Bash(npm:*)`, `Bash(security:*)`, `Bash(open:*)`. **Ce n'est pas un bac à sable** : Claude Code compare une
  règle au **préfixe** de la commande ; un agent déterminé les contourne (un script, un interpréteur en une
  ligne, un binaire renommé ou chemin complet, un alias, une bibliothèque). Elles arrêtent l'usage direct.
- **L'audit après exécution** des commandes et des chemins (voir « Audit de contamination »), qui **détecte**
  les formes directes sans rien empêcher.
- **Le bundle n'a aucun distant ni lien avec le dépôt de développement** : un `git push` depuis le bundle ne
  mène nulle part ; le bras peut en revanche agir sur le vrai dépôt s'il y accède par un autre chemin.

**Non appliqué, dit explicitement** : toute interdiction de lecture, de réseau ou d'identifiants pour un bras
cloud (Claude Code ne s'authentifie pas sous `sandbox-exec`, cause non établie), la garde R1, la preuve qu'un
bras n'a pas démarré un autre agent (les tokens d'un sous-processus `claude` ne sont pas lus). Le mainteneur a
accepté explicitement ce risque résiduel le 2026-10-05, avec les atténuations ci-dessus (règles d'interdiction
de commandes, audit de contamination, bundle sans dépôt distant).

## Audit de contamination

Après chaque exécution (locale ou cloud, une fois le flux conservé), le lanceur lit les appels d'outils du
flux (`tool_use` Claude, `tool_execution_start` omp) et, quand le flux les expose, les **résultats d'outils**
(blocs `tool_result` Claude, `tool_execution_end` omp). Des arguments d'un appel, il ne lit que les **clés de
chemin** (`path`, `file_path`, `notebook_path`, `cwd`…, et le `pattern` d'un outil Glob/`find`, qui est un
chemin) et les **commandes** (`command`, `cmd`), quel que soit l'outil : le texte qu'un bras écrit ou cherche
(`old_string`/`new_string` d'Edit, `content` de Write, `pattern` de Grep…) n'est pas un accès, même s'il mentionne
`~/.config` ou `~/.claude` (des dizaines de fichiers de ce dépôt, outillage, tests, docs, `AGENTS.md`, en contiennent). Il relève :

- **les chemins** : valeurs de clés de chemin, et dans une commande tout jeton qui ressemble à un chemin (`~`,
  `$HOME`, absolu, relatif). Dans une commande, `~` et `$HOME` ne sont développés que là où le shell les développe :
  un `~` entre guillemets (simples ou doubles) et un `$HOME` entre guillemets simples sont du texte
  (`grep -rn '~/.claude' .` est propre ; `cat ~/.config/foundry/registry.json`, `ls ~/.claude/plugins`,
  `cat "$HOME/.claude/x"` sont relevés). `~` et `$HOME` valent le HOME **qu'avait le bras** : pour un bras
  **local**, le HOME isolé de son dossier d'essai (`cat ~/.config/x` d'un bras local est propre ; un chemin absolu
  littéral sous le HOME réel reste relevé) ; pour un bras cloud, le HOME réel. Le **corps d'un `heredoc`** est du
  texte (`cat > tests/t.py <<'EOF'` … `"gh pr merge 12"` … `EOF`, ou une doc qui cite `~/.config/foundry`, sont
  propres), avec ou sans guillemets autour du délimiteur, `<<-` compris, plusieurs par commande ; la ligne qui
  porte le `<<` reste auditée (`cat > ~/.config/x <<EOF` est relevé) ; un corps qu'un **shell exécute**
  (`bash <<EOF`, `sh -s <<EOF`, `cat <<EOF | bash`, `source /dev/stdin <<EOF`) est audité comme une ligne de
  commande (commandes et chemins), pas celui de `cat`, `tee` ou `python3 -`, et avec un délimiteur **sans
  guillemets** (`<<EOF`) les `$(…)` et `` `…` `` du corps, que le shell exécute, le sont aussi ; un `<<` entre
  guillemets ou dans `$((…))` n'est pas un `heredoc`, et un `heredoc` jamais fermé reste audité tel quel. Un
  **commentaire** `#` (en début de mot) est du texte. Un chemin relatif se résout **contre le bundle**, ou contre le dossier d'un `cd` précédent de
  la même commande (`cd ~ && cat .claude/x`). Hors du bundle et du dossier d'essai, un chemin est relevé quand il
  tombe dans la liste sensible (celle d'un bras local : cache de plugins et configuration de l'utilisateur sous
  `~/.claude`, `.config`…, tout autre checkout ou worktree de ce dépôt, dossier d'état, dossiers des fichiers
  d'entrée, secrets du HOME) ; **et**, pour une clé de chemin ou une commande Bash, quand il se résout vers le HOME
  réel ou dessous, vers un dossier qui en contient un chemin sensible ou un ancêtre du bundle (`find ~`,
  `grep -r … ~`, `src/../../..`), ou, s'il est **relatif** au bundle, hors du bundle et du dossier d'essai
  (`../../x`) ; un chemin absolu hors du HOME n'est pas relevé par ce seul motif (`/usr/bin` reste propre) ; un
  jeton fait **uniquement de barres obliques** (`/`, `//`) est l'opérateur de division du code (`sum(v) / len(v)`
  dans un script Python en `heredoc` donnait à tort `/`, la racine, lors de l'essai réel final de Sonnet 5.5),
  **sauf** comme argument de `find`, `grep -r`/`-R`, `rg`, `ls`, `du`, `tree` ou `cat` dans un segment de commande,
  où c'est la racine et la règle large s'applique (`find / -name x -exec cat {} +` est relevé). **Limite
  connue** : un `/` nu qui atteint le shell autrement (un script, `echo / | xargs ls`) n'est pas vu ;
- **les résultats d'outils** montrant un chemin **littéral absolu** sous une racine sensible
  (`tool_result:<chemin>`, par exemple `/Users/<u>/.claude/plugins/…` affiché par `find`, `ls` ou `cat`) ; `~`,
  `$HOME` ou `${HOME}` dans le texte d'un résultat ne sont pas développés (un fichier du bundle qui cite
  `~/.config/foundry/registry.json` est du texte, pas un accès). Pour un bras **cloud**, son **propre dossier
  de session** `<projects_dir>/*/<id de session>/` (`projects_dir` de `session_log`, id donné par le lanceur) est
  permis, dans un résultat comme dans un appel : Claude Code y enregistre une sortie d'outil trop grande pour le
  flux (« Output too large. Full output saved to: …/tool-results/… ») que le bras relit ensuite ; le dossier
  d'une **autre** session, le journal `<id>.jsonl` lui-même et le reste de `~/.claude` restent relevés. Un
  chemin dont le **texte littéral** figure dans les fichiers du bundle (`base_literals` : chemins absolus sous le
  HOME réel relevés par `git grep` dans le bundle **à sa construction**, avant le bras et avant tout patch d'une
  tentative précédente, donc non falsifiables par le bras) n'est pas relevé **dans un résultat** : c'est le texte
  d'un fichier lu. Les clés de chemin et les commandes qui le nomment restent auditées sans changement
  (`cat /Users/<u>/.codex/x` ou un Read de ce chemin sont relevés), et un chemin du résultat absent de la base
  (un sous-chemin compris) l'est aussi. Cas connu : la base de la tâche de tamisage **PR 83** (`f740bebe48`)
  contient 12 chemins `/Users/<mainteneur>/.codex/…` dans `docs/qualification/pat-61-*` et `pat-62-*` ;
- **les commandes** (`command:<étiquette>`) : tout appel Bash dont la commande, après une normalisation simple
  (corps de `heredoc` et commentaires retirés comme ci-dessus, découpe sur `&&`, `||`, `;`, `|`, `&`, `(`, `)` et
  les fins de ligne **hors guillemets** seulement — `grep -E "claude|codex" src` ou un message de commit
  `"…; gh …"` ne lancent rien —, guillemets retirés, chaque `$(…)` et `` `…` `` hors guillemets ou entre
  guillemets doubles lu comme une commande, retrait de `VAR=…`, `env`, `sudo`, `command`, `exec`, `nohup`,
  `time`, `nice`, `xargs`, `timeout` et des mots-clés `{`, `!`, `if`, `then`, `else`, `elif`, `do`, `while`,
  `until`, descente dans le script de `sh|bash|zsh|dash -c` (commandes et chemins),
  nom de base de l'exécutable), lance `gh`, `curl`, `wget`, `claude`, `codex`, `omp`, `ssh`, `scp`, `nc`, `security`, `open`,
  `npm`, `git push|remote|clone|fetch|pull` (options `-C`/`-c` sautées), `python -m http…`, `python -m pip install`
  ou `pip install`.

Un relevé rend l'enregistrement **`contaminated: true`**, avec `contamination.paths` (le HOME est écrit `~`) et
`contamination.commands`, `outcome: contaminated`, `accepted` nul : la tâche est **indécise** pour ce parcours,
jamais acceptée. Le
verdict du juge d'une tentative locale reste consigné mais n'est pas retenu ; un bras cloud contaminé n'est ni
jugé ni relu (pas de dépense de plus), son coût reste compté. Une contamination n'est jamais rejouée ni
corrigée (pas de seconde chance) : le tamis reste incomplet (`undecided_tasks`, `selected` nul), un parcours dont
une tâche est contaminée a une qualité et une économie `unavailable`, `report.contaminated` liste chaque cas. Pour
le parcours C, une tentative locale contaminée est relayée par le cloud comme une tentative refusée (elle ne
compte pas comme réussite locale) ; une revue contaminée laisse la tâche indécise sans relais. **Lecture
refusée par le bac à sable local** : pour un bras local lancé sous le profil qui refuse le HOME
(`sandbox_denied`), un chemin que ce profil rend illisible (sous le HOME réel hors de la liste d'autorisation, ou
sous une entrée de la liste de refus explicite) est une tentative **bloquée**, pas un accès : l'exemption est décidée sur le chemin, pas sur un refus observé, et la tentative n'est pas relevée (un `ls ~/.config` honnête ne rend pas le tamis incomplet). Cette
exemption ne vaut que pour les lectures que le bac à sable refuse : le profil est `(allow default)`, donc les
services système restent joignables (`launchctl submit … -- cat <fichier du HOME>` puis lecture de la sortie,
`osascript` pilotant une autre application lisent hors du bac à sable) ; `launchctl` et `osascript` sont des
exécutables interdits (`command:launchctl`, `command:osascript`, pour un bras local comme cloud), au même titre que `gh`, `curl`, `wget`, `claude`, `codex`, `omp`, `ssh`, `scp`, `nc`, `security`, `open`, `npm`. Cette liste contre les contournements par un service système est un moindre effort : restent non listés `crontab`, `at`, un serveur `tmux`/`screen` lancé hors du bac à sable, `shortcuts run` et `automator`. Le dossier de
tentative (parent du bundle, géré par le lanceur : bundle et scratch) est autorisé lui-même (`ls ..` est propre) ;
son parent (la racine de travail, qui peut contenir un autre bundle) et au-dessus restent relevés. Les commandes interdites, les remontées hors du bundle vers un endroit lisible et les chemins
lisibles (environnement du harnais) restent audités. Un bras cloud n'a pas ce bac à sable. **Fragilité à
connaître** : une seule contamination rend le tamis entier incomplet sans rejeu (nouvel identifiant de campagne
nécessaire). Pour un bras **cloud**, un appel **refusé** par une règle de `cloud_bash_deny` (Claude Code répond
« Permission to use … has been denied by your rule ») est encore relevé comme contamination alors que rien n'a
été exécuté, et le corps d'un heredoc passé à un interpréteur (`python3 - <<'PY'`) n'est pas audité (un chemin
qu'il ouvre n'est pas vu) : deux points propres aux bras cloud, donc à la comparaison et pas au tamis, à traiter
avant la comparaison. **Limites** :
c'est un audit, pas une interdiction (la commande ou la lecture a eu lieu, ou a été tentée) ; il ne voit que ce que le flux montre
(un chemin ou une commande construits à l'exécution, cachés dans un script ou un interpréteur, un alias, un
processus fils, un sous-agent ne le sont pas) ; l'analyse d'une ligne de commande n'est pas un analyseur de shell
(`eval "…"`, `find -exec`, `watch "…"`, un `$'…'` ne sont pas suivis ; un chemin dans un argument entre guillemets
est encore lu morceau par morceau, donc `grep "x ../../y" src` ou `echo $HOME` restent relevés : texte lu comme
un accès, choix conservateur) ;
une commande `npm` légitime (tâche JavaScript) est relevée comme contaminée (choix conservateur) ; il porte sur
le flux du lanceur (mêmes appels d'outils que la transcription de session de l'hôte). Une contamination
possible mais non détectée reste donc une limite du résultat des bras cloud. **Le flux du harnais neutre
(mini-swe-agent) n'est pas audité** : sa trajectoire (fichier `trajectory`) n'est pas analysée, seul son flux de
sortie l'est, qui ne contient pas d'appels d'outils au format lu ; l'atténuation est le profil `sandbox-exec` qui
refuse la lecture du HOME réel aux bras locaux (`deny_home_by_default`). Une exécution cloud refusée sur son
événement `init` (outils hors liste) est quand même auditée : la contamination éventuelle est consignée avec le
refus (`status: tool_error`, `outcome: contaminated`), et l'enregistrement n'est alors jamais rejoué. De même,
une **coupure** (signal, panne d'outil) qui tombe **après l'audit** d'une exécution (le signal reçu pendant
l'audit est différé jusqu'à ce que son résultat soit gardé) donne `status: interrupted` ou `tool_error` **et**
`outcome: contaminated` avec la contamination déjà trouvée, pour une tentative locale, un tour cloud ou la revue
du parcours C, ainsi qu'une tentative locale réglée mais pas encore écrite : jamais rejouée. Une coupure
**avant** la fin de l'audit laisse la tentative vide, rejouable une fois (le flux partiel n'est pas audité).

Le pilote `local_harness` est la commande `omp` (18.6.1) éprouvée le 2026-10-05 (voir ci-dessus).

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
  prise : elle peut se rejouer une fois (reprise bornée), sauf si un verdict (ou une revue) était déjà reçu : il
  est alors écrit sur l'enregistrement coupé, qui est décidé.
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
  elle est rejouable une fois par une relance sous les mêmes empreintes, voir « Reprise bornée ») ;
  c'est le registre qui le dit à `report` (voir Rapport : `ledger.unsettled_starts`, avertissements,
  décision `inconclusive`).
- **Total premium d'un enregistrement** : connu seulement si les compteurs de **chaque** exécution cloud
  de `cloud_sessions` ont été lus ; une exécution inscrite au registre dont le coût n'a pas été relu rend
  le total nul (inconnu), jamais 0.
- **Arrêt sur plafond** : le tour coupé (y compris une correction ou une reprise qui n'a pas pu démarrer)
  reçoit un enregistrement `stopped_by_cap` : la tâche n'est pas décidée, ce n'est pas un refus.
- **Panne d'outil** : une `CorpusError`/`RunnerError` n'est jamais un verdict (sauf une panne causée par le candidat, qui est un refus : voir « Nulle »). Elle écrit un
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
- Non mesuré ici : interventions humaines imprévues (à consigner à la main). Les empreintes des poids sont
  consignées dans la configuration, pas recalculées (voir Préflight).

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
  lancement ultérieur : elle n'est plus un avertissement une fois son rejeu enregistré) et chaque préflight réussi suivi d'aucun démarrage de tentative avant le préflight
  ou le lancement suivant (un préflight suivi d'un `stopped` propre n'en est pas un). Chaque entrée porte
  le mode de la session qui l'a écrite et un `warning` nommant la tentative ou le préflight. Pour le tamis,
  ces avertissements figurent dans `screening.warnings` : `selected` reste (le résultat des enregistrements
  est intact) mais le tamis n'est pas présenté comme propre. Pour la comparaison, ils figurent dans
  `comparison.warnings` et la décision est `inconclusive` (jamais `retained` ni `keep_cloud`).
- **Candidat comparé** : `report` refuse (code 2) une comparaison dont le candidat local (parcours `C` ou
  `N`) n'est pas celui que le tamis a désigné par la règle préenregistrée (`selected`) ; un tamis incomplet
  ou sans vainqueur ne désigne personne, donc refuse aussi. `compare` fait la même vérification au
  démarrage, avant toute réservation, pour les parcours `C`/`N` (`A` et `B` n'ont pas de candidat local), **mais
  elle ne fonctionne que si le tamis et la comparaison partagent l'identifiant de campagne** (mêmes fichiers
  de résultats : le tamis d'un autre identifiant n'est pas vu). Quand les résultats de **cet** identifiant ne
  contiennent aucune tentative de tamis, `compare` **refuse** de démarrer une comparaison `C`/`N` (code 2),
  sauf si `--screening-campaign <id>` désigne un tamis **terminé** d'un autre identifiant de campagne, lu en
  **lecture seule** dans le même dossier d'état (`results-<id>.jsonl`), produit sous la même configuration de
  campagne et de même nature (à blanc ou réel), et dont `selected` est le candidat demandé. Côté `report`, sans
  résultats de tamis dans les fichiers le contrôle est impossible : `comparison.screening_selected` vaut
  `screening_results_not_available` (rien n'est supposé). La classe `Runner` garde un mode permissif
  (`require_screening=False`) pour les tests ; la CLI exige toujours le tamis.
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
  refus ; **appliqué à chaque pilote local, toujours** : un pilote local `sandbox: false` est refusé au
  chargement ; **les pilotes cloud épinglés tournent sans** (`sandbox: false`, raison dans la configuration :
  Claude Code ne s'authentifie pas sous `sandbox-exec`) : leur seul confinement est alors le bundle comme
  répertoire de travail, l'environnement en liste blanche, les règles `cloud_bash_deny` et l'audit
  (voir « Exposition des bras cloud ») ; un vrai lancement exige `sandbox-exec` dès qu'un pilote sandboxé
  sert) : écriture interdite hors du bundle et du dossier d'essai (plus `extra_write` pour un pilote
  cloud) ; **lecture interdite par défaut sur tout le HOME réel** pour un pilote local
  (`isolation.deny_home_by_default`, vrai par défaut) : le profil refuse `file-read*` sur le HOME puis ne
  rend que le bundle, le dossier d'essai, les entrées de `isolation.allow_read_home` (vide par défaut) et la
  racine de l'environnement de l'exécutable du harnais quand elle est dans le HOME, plus la lecture des
  **métadonnées** (`stat`, jamais le contenu ni le listage) des dossiers ancêtres de ces chemins jusqu'au HOME ;
  le dossier de travail est en général hors du HOME réel. **La liste de refus explicite reste une seconde
  couche, placée après l'autorisation (la dernière règle qui correspond gagne)** : `read_deny_list` donne chaque
  checkout et worktree de ce dépôt que `git worktree list` énumère, le dossier d'état, le dossier parent de
  chacun des fichiers `--envelope`, `--campaign`, `--snapshot`, `--manifest` (à garder à part de `--work-root`,
  sinon le lanceur refuse de générer le profil), et dans le HOME **réel** les entrées de
  `isolation.deny_read_home` : pour un pilote local `.claude` (dont le cache de plugins qui contient les
  tests fusionnés), `.claude.json`, `.codex`, `.config`, `.ssh`, `.gnupg`, `.aws`, `.netrc`,
  `Library/Keychains`, `.git-credentials`, `.npmrc`, `.pypirc`, `.docker`, `.kube`, `.zsh_history`,
  `.bash_history`, `.python_history` ; la liste `cloud` (même liste sans `.claude`, `.claude.json` ni
  `.config` entier, avec `.config/foundry`) n'est **pas appliquée** à un bras cloud (pas de bac à sable) : elle
  ne sert qu'à nommer ce que l'audit surveille. Le profil généré, qui nomme tous les chemins refusés, est écrit
  dans un dossier temporaire lui-même interdit en lecture au candidat (sonde testée). Réseau limité à la
  boucle locale pour un pilote local. Un vrai lancement exige `sandbox-exec` (refusé ailleurs).
  **Statut du refus par défaut** : implémenté, testé sur le profil (ordre refus HOME, autorisations, refus
  explicite ; métadonnées des ancêtres seulement) et sous un vrai `sandbox-exec` avec un faux HOME (un fichier
  quelconque du HOME est refusé, une entrée de la liste d'autorisation et le bundle sont lisibles). **Essayé le
  2026-10-05 avec le vrai `omp` 18.6.1 et mini-swe-agent 2.4.6** (`allow_read_home` vide) : les deux réussissent,
  le refus par défaut est confirmé ; la liste explicite (37 entrées de refus sur la machine du mainteneur, plus le
  dossier du profil) a aussi été essayée avec le vrai harnais local.
  **Trousseau non couvert** : `(allow default)` laisse l'accès Mach au démon de sécurité ; refuser la
  lecture de `Library/Keychains` n'empêche donc **pas** une requête au trousseau (`security
  find-generic-password`, API Keychain). Rien n'est imposé là-dessus ici : voir les préconditions PAT-109.
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
  empreintes (chemin, taille, mtime) du bundle à 0,2 s d'écart et, si elles diffèrent, ne juge pas : la
  tentative est refusée (`REFUSED`, `candidate_fault`, voir « Nulle »). Un enfant échappé qui ne fait rien ne se
  voit pas. **Limite connue, non corrigée ici** : le profil d'un bras local part de `(allow default)`, donc un bras
  local peut envoyer un signal au lanceur (`pkill`, `kill`) ; une coupure ainsi provoquée donne une tentative
  **nulle**, rejouée **au plus une fois** et listée dans `report.void_attempts` (une seconde coupure laisse la
  tâche indécise). Durcir le profil demanderait un nouvel essai réel avec un modèle chargé.
- **Git sur un bundle** : toute commande git du lanceur sur un bundle neutralise la configuration
  (`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` vers `/dev/null`, `core.fsmonitor=false`,
  `core.hooksPath=/dev/null`, `protocol.file.allow=never`, `core.attributesFile=/dev/null`, `--no-ext-diff`,
  `--no-textconv`) et refuse un bundle dont `.git` n'est plus un dossier simple, dont `.git/config`,
  `.git/info/attributes` ou un `.gitattributes` a changé depuis le commit racine. Le patch est pris contre
  le SHA du commit racine enregistré à la construction (un bras qui commite donne son patch) et les
  fichiers nouveaux y figurent ; il est appliqué avec `--index` pour la revue, **sans** ses fichiers sous un
  dossier `.claude/` (à toute profondeur) : l'hôte du relecteur les chargerait comme réglages de projet. Les
  chemins écartés sont consignés dans `review_excluded` de l'enregistrement (absent quand rien n'est écarté) ; le
  juge, lui, a vu le bundle complet.
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

Ce que l'isolement **n'impose pas** : pour un bras **local**, tout ce qui n'est pas sous le HOME réel
(le reste du disque, par exemple une copie des tests fusionnés ailleurs) reste lisible, et si
`deny_home_by_default` est mis à `false` après un échec avec le vrai harnais, tout chemin non nommé de la liste
explicite l'est aussi ; un enfant sorti du groupe par `setsid` ; le code produit reste importé dans le
processus de test (limite du juge) ; le harnais local et le harnais neutre sont éprouvés (PAT-111) mais leur
configuration réelle n'est pas auditée. Pour un bras **cloud**, **rien de la lecture, du réseau ni des
identifiants n'est imposé** : voir « Exposition des bras cloud » (lecture de tout le HOME dont `~/.claude` et le
cache de plugins, réseau ouvert, identifiants implicites, pas de garde R1 ; **le tracker, les secrets et la
fusion ne sont pas protégés par le lanceur** ; seuls les règles Bash au mieux, l'audit après coup et l'absence de
distant du bundle s'interposent).

## Confiance dans les fichiers d'état

Le registre et le fichier de résultats sont des fichiers **de l'opérateur, sans protection d'intégrité**
(ni signature, ni chaînage) : supprimer une ligne de résultats permet de rejouer une tentative décidée, supprimer
une ligne du registre remet un plafond à zéro, et le croisement de `report` ne détecte que les
incohérences entre les deux fichiers, pas une suppression cohérente dans les deux. Un bras **local** ne peut
ni les lire ni les écrire (`sandbox-exec` : dossier d'état interdit en lecture, écriture limitée au bundle et au
dossier d'essai). Un bras **cloud**, sans `sandbox-exec`, **peut lire et écrire** le registre, les résultats et
les flux conservés (`streams/`) : ce qui tient, c'est seulement que l'audit relève tout appel d'outil ou ligne de
commande qui **nomme** un chemin du dossier d'état (lecture ou écriture, l'enregistrement devient `contaminated`),
sans rien empêcher ni réparer ; une écriture faite par un script, un interpréteur ou un processus fils n'est
**pas détectée** (limite connue). Les empreintes vérifiées au démarrage (`campaign_sha256`, `manifest_sha256`,
`envelope_sha256` de chaque ligne `session_started` du registre et de chaque résultat) refusent un état écrit
sous une autre configuration, pas une ligne supprimée ou modifiée : ce n'est pas un contrôle d'intégrité.
L'honnêteté de la campagne repose donc sur l'opérateur : un seul dossier d'état par
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

## Préconditions pour PAT-109 : état après PAT-111

Faites par PAT-111 (voir « Pilotes épinglés ») : épingler chaque pilote par un essai réel (les cinq ont passé
leur essai ; l'argv final des trois pilotes cloud et le drapeau de borne d'étapes du harnais neutre ont aussi
été essayés le 2026-10-05) ; vérifier la disposition des journaux de session (compteurs égaux à ceux de l'hôte, sous la
condition de l'assertion d'outils) ; consigner les empreintes des poids, le gabarit et les paramètres de
génération ; essayer la liste de refus avec le vrai harnais local, puis le refus par défaut du HOME avec `omp`
18.6.1 et mini-swe-agent 2.4.6 (2026-10-05) ; relever les champs de `lms ps --json` ;
traiter le biais des bras cloud par l'audit de contamination et des règles Bash au mieux ; obtenir
l'acceptation explicite par le mainteneur de l'exposition résiduelle des bras cloud (donnée le 2026-10-05, voir
« Exposition des bras cloud »). **Restent ouvertes** : la mémoire globale `CLAUDE.md`
éventuellement chargée par le bras cloud ; **trouver où le jeton du tracker est stocké** (fichier, trousseau,
variable) et, s'il est au trousseau, refuser ce service dans le profil, par exemple `(deny mach-lookup
(global-name "com.apple.SecurityServer") …)`, à condition que le harnais fonctionne encore ainsi (à vérifier par
test de fumée : non essayé ici).

## Statut documentaire (AGENTS.md R5)

Artefacts documentés ici : la surface CLI de `foundry.local_first_runner` (dont `--screening-campaign`, la reprise
bornée et le refus d'une configuration différente à `report`), la configuration de campagne
(`pat-19-campaign-v1.json` : `isolation` dont `deny_home_by_default` et `allow_read_home`, `cloud_bash_deny`,
`non_protocol_choices`, `session_log.layout_verified`, et depuis PAT-111 les clés de pilote
`sandbox`/`sandbox_reason`/`evidence`/`executable`/`env_set`/`make_dirs`/`trajectory`,
le format de flux `claude-stream-json`, les clés de candidat `lm_studio_key`/`quantization`/`min_context`/
`load_command`/`file_sha256`/…, le fichier de preuve `pat-19-preflight-2026-10-05.json` dont `command_tried`,
`pinned_final_argv` et `lms_ps_json_observed`), les refus de préflight
`loaded_context_*`/`loaded_quantization_*`/`loaded_model_key_*`, l'enregistrement `deny_home_trial` de chaque candidat (dont `smoke.deny_home_evidence`), la clé de pilote `binary_version`, le paramètre
`sandbox_denied` et `attempt_dir` de `audit_transcript`, `launchctl`/`osascript` dans les exécutables interdits,
l'enregistrement `contaminated`/
`contamination` (`paths`, `commands`) et `report.contaminated`, les règles de l'audit (corps de `heredoc`,
commentaires, séparateurs entre guillemets, `sh -c`, HOME isolé d'un bras local, dossier de session propre d'un
bras cloud, `base_literals`, contamination gardée sur une coupure après l'audit), `review_excluded`, la note
`candidate_fault` (pannes du bundle après l'exécution du bras), `report.void_attempts` (dont les sessions cloud
sans enregistrement), `local.harness_executable`, `local.step_limit_hit`, le format
d'enveloppe et de registre (`dry_run`, empreintes, `settled` apparié), le schéma des résultats (`tool_error`,
`interrupted` avec `judge` quand la coupure suit un verdict, `stopped_by_cap`, `cloud_sessions`,
`premium.by_model`), les règles du rapport (registre
obligatoire, invariant registre/résultats), la confiance accordée aux fichiers d'état, l'exposition des bras
cloud et le périmètre de l'isolement. Aucune constante publique, option de `foundry_cli.py`, clé de
configuration produit ni table de routage n'a changé (la table de traduction des modèles Claude de
`routing_facades.py` n'est pas touchée : les identifiants complets des pilotes sont des données de campagne).
**Documenté et non appliqué mécaniquement** (dit explicitement) : toute interdiction de lecture, de réseau ou
d'identifiants pour un bras cloud ; l'audit du flux du harnais neutre (trajectoire non analysée) ; l'interdiction
pour un bras local de signaler le lanceur ; le recalcul des empreintes de poids au préflight ; la borne
d'étapes du harnais neutre **en cours d'exécution** (transmise au harnais et vérifiée a posteriori) ; la
détection des chemins ou commandes construits à l'exécution par l'audit ; toute protection du dossier d'état
contre un bras cloud (lecture ou écriture : seul un chemin nommé dans un appel est relevé) ; la fenêtre de quelques instructions
entre le retour du juge et l'affectation de son verdict. L'application mécanique de R5 reste celle de
FOUNDRY-123.

## Protocole v2 : exploration en lecture seule (PAT-114)

Protocole gelé : [`pat-19-protocol-v2.md`](pat-19-protocol-v2.md) ; configuration : `pat-19-campaign-v2.json` ; vérité terrain des 12 tâches : `pat-19-exploration-truth-v2.json`. Le lanceur v1 est étendu, pas doublé (FOUNDRY-ADR-0019) : mêmes enveloppe, registre, reprise bornée, signaux, isolement, audit de contamination, préflight, pilotes cloud, bundles et revue. **Rien de la v1 ne change de comportement** (les tests de la v1 passent tels quels) : `pat-19-protocol-v1.md`, `pat-19-campaign-v1.json` et les résultats v1 ne sont pas modifiés. Les tests du diff n'utilisent que des bras factices (aucun modèle, harnais ni appel cloud) ; le coordinateur a lancé deux essais réels sur un dépôt jouet le 2026-10-06 (`pat-19-preflight-v2-2026-10-06.json`).

**Fichiers.** `../../tooling/foundry/local_first_exploration.py` (pur : vérité terrain, format et lecture du rapport, juge de localisation, règle du tamis, contrôle de machine dédiée, règles de qualité et d'économie, rendu du rapport dans l'énoncé) ; `local_first_runner.py` (modes, pilotes, enregistrements, rapport) ; tests `test_local_first_exploration.py` (pur, y compris les 12 vrais diffs fusionnés) et `test_local_first_exploration_runner.py` (bras factices).

```
python3 -m foundry.local_first_runner screen-exploration  --campaign <cfg-v2> --envelope <env> --state-dir <dir> --work-root <dir> \
    --repo <clone complet> --snapshot <snapshot> --manifest <manifeste> --candidate <id> [<id> ...] [--dry-run] [--sandbox]
python3 -m foundry.local_first_runner compare-exploration ... --candidate <id> [--paths A,L,E] [--screening-campaign <id>]
python3 -m foundry.local_first_runner preflight --campaign <cfg-v2> --candidate <id> --dedicated
```

- **Modes** `screen_exploration` et `compare_exploration` (enveloppe : `allowed_modes`) ; une configuration `foundry.local-first-campaign.v2` n'est acceptée que par eux, une v1 que par `screen` et `compare` (refus, code 2). `screen_exploration` ne peut jamais lancer d'exécution cloud (comme `screen`).
- **Pilotes** : types `local_explorer` (local : toujours dans le bac à sable, HOME isolé, boucle locale) et `cloud_explorer` (cloud : règles Bash de la v1 obligatoires, `Edit` et `Write` interdits). Le chargement refuse un explorateur local sans exactement un `--tools=` limité à `read,grep,glob`, et un explorateur cloud qui n'interdit pas `Edit` et `Write` : le jeu d'outils en lecture seule ne se perd pas en silence. Les deux sont `verified: true` (essais réels du 2026-10-06 sur un dépôt jouet, preuves `pat-19-preflight-v2-2026-10-06.json`) ; les noms d'outils d'`omp` ont été corrigés par cet essai (`find` et `ls` refusés).
- **Bundle en lecture seule** : l'exploration locale passe `workdir_writable=False` à `execute_driver` ; le profil `sandbox-exec` n'autorise l'écriture que dans le dossier d'essai de la tentative (le bundle reste lisible). Pas de contrôle de quiétude ni de patch ; en revanche `git status --porcelain` du bundle est relevé avant et après chaque exploration (locale et cloud) : un bundle modifié ou illisible refuse l'exploration (`BUNDLE_MODIFIED`, score 0, `exploration.bundle_modified`).
- **Enregistrements** (`foundry.local-first-result.v1`) : tamis = chemin `XS`, segment `local`, tâche `screening`, tentative 0 ; comparaison = chemins `A` (segment `cloud`), `L` et `E` (segment `explore` puis `cloud`). Le verdict du juge de localisation est le champ `judge` (`verdict` : `SCORED`, `REFUSED`, `CONTAMINATED` ; `file_recall`, `file_precision`, `function_recall`, `counts` entiers), donc **les règles de reprise de la v1 s'appliquent telles quelles** : décidée (verdict reçu) = sautée, jamais rejouée ; nulle (panne du lanceur avant le bras, ou coupure avant le verdict) = rejouée une fois, avec `replay_of`. Un rapport absent ou illisible est un **refus** décidé (score 0), jamais une tentative nulle. `exploration` porte le rapport normalisé (rejoué à la reprise pour construire l'énoncé), sa source (`file` ou `final_message`), le motif de refus et le score. L'explorateur cloud (un enregistrement `E`/`explore`, une exécution, jamais rejouée une fois enregistrée ; coupée avant son enregistrement : rejouée une fois sous les plafonds).
- **Rapport lu** dans `<dossier d'essai>/report.json` si le bras l'a écrit, sinon dans le message final du flux (`result` d'un flux Claude, dernier `message_end` assistant d'un flux `omp`).
- **Énoncé des bras L et E** : le rapport rendu (`render_report_section`, coupé à `exploration.report_render_limits` avec une mention visible) suit le pied d'énoncé dans le `TASK.md` de l'implémenteur et de ses corrections (`_bundle(statement_extra=...)`, validé dans la base commitée comme le pied) ; le relecteur reçoit l'énoncé simple. Le bras A reçoit l'énoncé simple.
- **Machine dédiée** : `preflight(..., dedicated=True)` (sans effet en v1) ajoute `facts.dedicated_machine` (valeurs observées) et les refus `dedicated_machine_process_over_<N>gib:<nom>:<Mio>`, `dedicated_machine_free_memory_below_minimum:<n><<min>`, `dedicated_machine_ps_unavailable`, `dedicated_machine_memory_pressure_unavailable`. La seule sonde nouvelle est la lecture de la sortie de `ps -axo rss=,command=` et `memory_pressure`, déjà autorisés : `READ_ONLY_COMMANDS` ne change pas.
- **Rapport** (`report`, détecté par le schéma de la configuration) : `exploration_screening` et `exploration_comparison` à la place de `screening` et `comparison` ; le registre reste obligatoire et croisé avec les résultats comme en v1 ; l'invariant « chaque session cloud est nommée par exactement un enregistrement » vaut aussi pour les explorations E. Le candidat comparé (bras L) doit être celui que le tamis v2 a retenu (`compare-exploration` le vérifie au démarrage, `--screening-campaign` comme en v1).
- **Tamis v2** : règle sur le rappel moyen de **fonctions** (principal, seuil d'arrêt 0,5 inclus), précision moyenne de fichiers ≥ 0,5 ; la table du rapport donne le rappel de fonctions en premier, puis précision et rappel de fichiers (mesurés, ne décident pas) ; clé de configuration `rules.exploration_screening.retained_function_recall_min`.
- **Décision** : voir le protocole (section 6) ; une campagne partielle est `inconclusive`, un `fail` d'une campagne complète est `keep_cloud`, E n'est jamais recommandé.

Modifications du code v1 partagé (sans effet sur la v1) : `MODES`, `DRIVER_KINDS`, `LOCAL_KINDS`, `CLOUD_KINDS`, `IMPLEMENTER_DRIVER` (clés `L` et `E`), `Ledger.reserve_cloud` et `cloud_execution` (autorisés aussi en `compare_exploration`, avec un délai `seconds_key`), `_bundle` et `cloud_path` (paramètre `statement_extra`), `preflight` (paramètre `dedicated`), `execute_driver` (paramètre `workdir_writable`), `_check_selected` (chemin et règle de tamis selon le mode), `report` (schéma v2).

**Statut documentaire (R5)** : voir la section 12 de `pat-19-protocol-v2.md` (surface CLI, schéma de configuration v2, types de pilote, chemins de résultats, module et fichier de vérité terrain, contrôle de machine dédiée). Non appliqué mécaniquement, dit explicitement : la lecture seule de l'explorateur cloud (Bash ouvert, pas de bac à sable : bundle inchangé observé après coup, non garanti).

**Compléments v2 (revue du 2026-10-06).** *Vérité terrain* : source unique = le fichier versé `pat-19-exploration-truth-v2.json`, dont le sha256 est dans `exploration.ground_truth` de la configuration ; le chargement de la configuration refuse un fichier différent (code 2, avant toute tentative) ; l'algorithme de diff, l'heuristique d'indentation et les renommages sont épinglés pour son calcul ; `all_changed_files` sert la précision. *Plafond* : 10 fonctions comptées et transmises (`MAX_FUNCTIONS`, `report_render_limits.max_functions` doit valoir 10). *Bornes* : le prompt annonce les bornes ; une exploration coupée par le temps ou les étapes est un refus (`BOUND_HIT`) même avec un brouillon JSON ; une exploration dont l'enveloppe raccourcirait le temps sous la borne n'est pas démarrée (arrêt `cap_reached:wall_clock_seconds`). *Tamis* : complet seulement avec les cinq candidats gelés (`rules.exploration_screening.candidates`), seuil testé avant l'égalité, égalité résiduelle départagée par l'ordre gelé ; `report` donne `missing_candidates`. *Machine dédiée* : mémoire libre ≥ 35 %, refaite juste avant chaque exploration locale du bras L. *Chargeur* : un seul drapeau `--tools=` (en un argument), pas de second drapeau d'outils.
