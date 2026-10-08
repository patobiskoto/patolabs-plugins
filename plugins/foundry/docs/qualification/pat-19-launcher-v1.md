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

> Ce qui suit décrit les protocoles v1 à v4 (`bypassPermissions`, aucun refus de lecture). Sous la clé
> `isolation.cloud_native_sandbox` d'un protocole v5 ou postérieur, les lectures du shell sont refusées par le système
> d'exploitation **seulement** sur le home, la racine de travail et les chemins de la liste de refus du lanceur qui ne sont
> sous aucun des deux (le reste du disque reste lisible par le shell : système, `/Volumes`, `/private/tmp`, `/Users`) ; les
> outils de fichiers reposent sur les permissions (observé pour Read et Write seulement, par l'essai du lanceur T2) : voir
> « Bac à sable natif des bras cloud (PAT-124) ».

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

### État Foundry d'un bras cloud (PAT-120, durcissement d'exécution avec un effet de bord déclaré)

Incident du 2026-10-07 (voir « Incident » de [`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md)) : un bras cloud a lancé les tests du dépôt à une ancienne base, un test posait `FOUNDRY_DATA_DIR` au lieu de `FOUNDRY_DATA`, et `registry._save` a écrasé le registre du mainteneur. Deux mesures, **pour tout lancement ultérieur de n'importe quel protocole** (v1, v2, v3 : implémenteur, corrections, relecteur, explorateur cloud). Les protocoles et configurations gelés ne changent pas et le sha256 de la configuration de campagne (coordonnée de décision) est inchangé ; ce n'est toutefois **pas un durcissement pur** : voir « Limite de comparabilité » ci-dessous.

- **`FOUNDRY_DATA` imposé par le lanceur aux bras cloud** : `execute_driver` pose, **pour tout pilote de type cloud** (`cloud_implementer`, `cloud_reviewer`, `cloud_explorer` : PAT-121, selon le type et non selon la politique de HOME du pilote), `FOUNDRY_DATA` sur `<dossier d'essai>/foundry-data`, un dossier créé vide à chaque exécution, **après** `env_set` (jamais pris de la configuration ni de l'environnement de l'hôte ; le chargeur refuse toujours `FOUNDRY_*` dans `env_set`, `env_allow` ne peut pas le surcharger). La règle `registry.data_dir` (`FOUNDRY_DATA` sinon `~/.config/foundry`) écrit donc dans le dossier d'essai. Les variables d'AGENTS.md R6 (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR`, `USER`) ne sont pas touchées. Les bras **locaux** gardent exactement leur environnement précédent (HOME isolé et bac à sable : aucun gain, et la variable a un effet de bord, voir plus bas) ; `isolated_environment` ne transmet de plus aucune variable `FOUNDRY_*` de l'hôte, même demandée par `env_allow` (le chargeur la refuse déjà : défense en profondeur, PAT-121).
- **Empreintes du registre réel** : avant et après chaque exécution cloud, le lanceur relève le sha256 des octets du `registry.json` **réel** aux **deux** endroits qu'un processus Foundry de l'hôte peut utiliser : `FOUNDRY_DATA` de l'hôte (s'il l'exporte, clé `host_foundry_data`) **et** `<HOME>/.config/foundry` (clé `home_config`), ou `absent` (`unreadable:<erreur>` si illisible). Un changement de l'un ou de l'autre : l'exécution est d'abord réglée (le **ledger** de la campagne la règle et compte le temps), l'audit de contamination est fait, puis l'**enregistrement** de la tentative est écrit avec `outcome: contaminated` (donc **non acceptée, indécise, jamais rejouée**), `billing_total: null` (jetons non lus : même forme qu'un refus de jeu d'outils ; « coût conservé » veut dire seulement que le ledger règle l'exécution), `foundry_state_changed: {before, after}` (les empreintes par endroit, **jamais le contenu**) et `unknown.foundry_state_changed`. Même enregistrement quand le changement est vu pendant la revue d'une tentative locale acceptée du chemin C. Puis l'enregistrement `stop` de raison **`foundry_state_changed`**, et le lanceur sort en **code 4** (message sur la sortie d'erreur). Rien n'est rejoué, relu ni réécrit : le lanceur **n'écrit ni ne restaure jamais** le registre ; l'opérateur enquête et restaure lui-même (sauvegarde dans [`pat-19-v3-operator.md`](pat-19-v3-operator.md)). Aucun nouveau bras cloud avant cette enquête. Les empreintes sont des sha256 qui finissent dans des fichiers de résultats peut-être versionnés : risque faible, le registre ne contient aucun identifiant secret.
- **Ce que le garde manque** (détection a posteriori, jamais prévention) : une exécution interrompue avant la lecture « après » (Ctrl-C, SIGTERM, exception dans `execute_driver`) n'est pas comparée, et le lancement suivant prend l'état modifié pour référence ; un changement entre deux exécutions ; un processus détaché qui écrit après la lecture ; une modification suivie d'une restauration ; une réécriture à contenu identique ; une comparaison au début d'un lancement avec l'empreinte de fin du lancement précédent n'est **pas** faite (elle demanderait une nouvelle entrée de ledger lue par le rapport : non fait, documenté). La prévention repose sur une variable d'environnement qu'un bras non confiné ou un test peut défaire : le registre reste inscriptible.
- **Limite restante** : un bras cloud non sandboxé peut encore écrire **ailleurs dans le vrai HOME** (`config.env`, marqueur d'installation, trousseau, `~/.claude`, etc., non surveillés). Les autres états écrits hors du bundle par les tests du dépôt **ne sont pas recensés : inconnu**. Un relevé rapide (`grep`) des tests actuels ne montre, hors `tests/conftest.py` (qui redirige `HOME`, vide `FOUNDRY_DATA`/`FOUNDRY_CONFIG`/`FOUNDRY_EXECUTION_RECEIPTS_DIR` et garde `~/.config/foundry` et `~/.config/orfeo-poc`, PAT-104), rien d'évident qui écrive sous HOME/XDG ; ce n'est pas une preuve d'exhaustivité, et les bundles sont à des **bases anciennes** dont les tests n'ont pas ce garde (c'est l'incident).
- **Limite de comparabilité** : la présence de `FOUNDRY_DATA` active le journal de télémétrie du produit (`tests/conftest.py`, `tooling/foundry/telemetry.py`). À partir de PAT-120, les exécutions que les bras font eux-mêmes des tests du dépôt voient donc `FOUNDRY_DATA` posée (journal de télémétrie activé **dans le dossier d'essai**, aux bases qui ont la télémétrie), contrairement aux exécutions des campagnes v1 à v3. L'effet sur ce que les bras observent, donc sur leurs tours et leurs jetons, est **inconnu**. Le juge n'est pas touché : son environnement n'a aucune variable `FOUNDRY_*` et un HOME temporaire (`local_first_corpus.py`).
- **Vérification du 2026-10-07 (coordinateur)** : `registry.py` à la base de chacune des 12 tâches du corpus, lu avec `git show` : les douze résolvent le dossier de données par `os.environ.get("FOUNDRY_DATA")` d'abord, donc la variable posée par le lanceur redirige l'écriture du registre du scénario de l'incident à chaque base du corpus ; une seule base (PR 83) a le garde `conftest` plus tardif qui vide la variable et redirige HOME.

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
son parent (la racine de travail, qui peut contenir un autre bundle) et au-dessus restent relevés. **Racine privée (PAT-121, `isolation.private_attempt_root`, v4 seulement)** : le dossier de tentative est alors `<racine>/private-<tentative>/<tentative>/`, son parent ne contient que lui, et ce parent est autorisé à son tour (`ls ../..` est propre) ; la racine de travail elle-même et toute autre tentative sous elle (chemin relatif ou absolu) sont ajoutées aux racines sensibles : les lire reste relevé, comme les tests cachés, le fichier de vérité, `~/.config` et le reste de l'audit, inchangés. Les commandes interdites, les remontées hors du bundle vers un endroit lisible et les chemins
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
  (`stopped`, raison `tool_error:…`, code de sortie 2 ; registre Foundry réel modifié par un bras cloud : raison `foundry_state_changed`, code 4, voir « État Foundry d'un bras cloud »). Une revue illisible est `review_unreadable` :
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

PAT-126 : protocole v5 (gelé le 2026-10-08) et pilote (protocoles `pat-19-protocol-v5` et `pat-19-protocol-v5-pilot`, clés `exploration.comparison_tasks` et `exploration.comparison_task_set`, `drivers.*.binary_version` vérifiée aussi pour les pilotes cloud, entrée `.config/git/ignore` de `isolation.allow_read_home`, fonction `_listed_tasks`, constantes `PROTOCOL_V5*` et `V5_*`, script et notice `pat-19-v5-operator.*`) : documentés dans « Protocole v5 (PAT-126) » (clé `isolation.audit_policy`, verbe `golden-check`, champs `audit.policy`/`journal`/`journal_by_role`/`refused_calls`, clé `audit_journal` du rapport, entrée `interpreters` du registre). PAT-120 : `FOUNDRY_DATA` imposé aux bras cloud, empreinte du registre réel, raison d'arrêt `foundry_state_changed`, champ d'enregistrement `foundry_state_changed`, code de sortie 4, fonction `registry_fingerprints` : documentés dans « État Foundry d'un bras cloud » ; sauvegarde opérateur dans `pat-19-v3-operator.md`. PAT-121 : protocole v4 (clés `correction_feedback`, `isolation.private_attempt_root`, `exploration.comparison_task_group`, `exploration.fixed_candidate`, option `failures` du juge, `FOUNDRY_DATA` imposé par type de pilote) : documentés dans « Protocole v4 », « Audit de contamination » et « État Foundry d'un bras cloud ». Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.

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

**Revue 2 (v2).** `report` expose `campaign_conclusion` (`retain_local_explorer`, `keep_cloud`, `keep_cloud_insufficient_evidence` pour une comparaison complète `inconclusive` sans `fail`, `incomplete_campaign`). Liste d'autorisation système de la machine dédiée réduite à `/System/Library/` etc. Une exploration dont la durée atteint `explorer_max_seconds` est un refus ; un `start_error` est nul (rejouable une fois). Le préflight dédié est lancé aussi au tout début de `compare-exploration` quand L est demandé. `main` sort en code 2 avec un message (sans trace) sur toute erreur de chargement de configuration, **y compris pour une configuration v1** (changement de comportement limité à ce cas d'erreur) ; `IMPLEMENTER_DRIVER.get(path, A)` garde le défaut v1 pour les chemins v1 (testé).

**Revue 3 (v2).** *Préflight de la comparaison* : le préflight de départ de `compare-exploration` est inscrit au registre avec `phase: "start"` (`Runner.preflight(candidate, phase=...)`) ; celui qui précède une exploration locale n'est pas refait quand le lanceur n'a rien écrit au registre depuis (`Ledger.appended`) ; dans `_unsettled_starts`, un préflight `phase: "start"` suivi d'un autre préflight du même lancement n'est pas un `preflight_without_attempt` (reprise après un préflight refusé, reprise après une exploration nulle, `--paths L` : plus de faux avertissement qui forçait `inconclusive`). Les entrées de la v1 n'ont jamais de `phase` : la v1 est inchangée. *Rapport du tamis* : `exploration_screening.campaign_conclusion` (`candidate_selected`, `keep_cloud`, `incomplete_screening`, `keep_cloud_insufficient_evidence` quand le tamis ne peut plus être complété sous la règle de reprise) et `not_completable_tasks` ; la règle de reprise est lue par la même fonction que le lanceur (`_resume_state`, extraite de `Runner._local_state` sans changement). *Rapport de la comparaison* : `awaiting_implementer` par bras (exploration enregistrée, implémenteur pas encore joué) : tâche indécise, campagne `incomplete_campaign`. *Configuration* : `premium_work_definition`, `refused_report_in_comparison`, `quality_bounds_on_undecided` et `economy_unavailable_cases` passent de `non_protocol_choices` à `exploration.protocol_coordinates`. *Nettoyage* : `Runner._explore_values` (doublon de `_values` en mode v2) et la valeur `report_file` (aucun gabarit ne l'utilisait) sont retirés.

**Résultats du tamis réel (PAT-115).** Essai du 2026-10-07 (`pat-19-xscreen-1`, arrêt « conserver le cloud ») : [`pat-19-exploration-results-v2.md`](pat-19-exploration-results-v2.md), pièces brutes dans `pat-19-runs/xscreen-1/`.

## Protocole v3 : budget élargi, un lancement par tâche (PAT-116)

Protocole gelé : [`pat-19-protocol-v3.md`](pat-19-protocol-v3.md) ; configuration : `pat-19-campaign-v3.json` ; boucle opérateur : [`pat-19-v3-operator.md`](pat-19-v3-operator.md). La v3 réutilise le mode exploration de la v2 (même schéma `foundry.local-first-campaign.v2`, mêmes modes `screen-exploration` et `compare-exploration`, même code) : elle n'est refusée que par les modes v1 et acceptée que par les modes exploration.

- **Configuration** : `protocol` vaut `pat-19-protocol-v3` ; le chargeur épingle alors les deux candidats (`qwen3.6-35b-a3b-mlx-4bit`, `qwen3-coder-30b-a3b-mlx-4bit`, dans cet ordre, à la fois dans `candidates` et `rules.exploration_screening.candidates`), les bornes `explorer_max_steps` 60 et `explorer_max_seconds` 900 et `exploration.one_task_per_launch: true`. Une dérive est refusée au chargement (code 2). `one_task_per_launch` est un booléen optionnel de toute configuration v2 (absent = faux : la v2 joue toutes ses tâches en un lancement, inchangée).
- **Une tâche par lancement** : avec la clé vraie, `screen-exploration` joue au plus une tâche non décidée (la première `fresh` ou `replay` dans l'ordre candidat puis tâche) et `compare-exploration` au plus une tâche de comparaison (tous les bras demandés), puis le lanceur sort en code 0 et écrit sur la sortie standard `pat19-v3: work_remains=yes` ou `pat19-v3: work_remains=no`. Au tamis, `yes` = une tâche à jouer reste ; à la comparaison, `yes` est prudent (une tâche suivante n'a pas encore d'enregistrement pour un bras demandé) : un lancement qui ne joue rien répond `no`, la boucle de l'opérateur se termine donc toujours. Un arrêt (plafond, code 3 ; préflight refusé, code 2) n'écrit pas la ligne. La reprise est inchangée (une tâche décidée n'est jamais rejouée).
- **Rechargement** : le lanceur ne charge ni ne décharge aucun modèle ; il vérifie, au préflight de chaque lancement, que le modèle attendu est chargé. Le rechargement avant chaque tâche est attesté par le script opérateur et par un préflight (une session) par tâche au registre, pas par le lanceur.
- **Rapport** : inchangé, il lit la liste des candidats de la configuration (un tamis v3 n'est complet qu'avec les deux).
- **Statut documentaire (R5)** : clé `exploration.one_task_per_launch`, ligne `pat19-v3: work_remains=…`, épinglages v3 du chargeur : ce document, le protocole v3 (section 6) et le CHANGELOG. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
- **Résultats réels (PAT-117)** : tamis `pat-19-x3screen-1` et comparaison `pat-19-x3compare-1`, exécutés le 2026-10-07 : [`pat-19-exploration-results-v3.md`](pat-19-exploration-results-v3.md) (pièces brutes dans `pat-19-runs/x3screen-1/` et `pat-19-runs/x3compare-1/`).

## Protocole v4 : deux défauts soupçonnés de l'instrument traités, un candidat fixé, bras A et L (PAT-121)

Protocole gelé : [`pat-19-protocol-v4.md`](pat-19-protocol-v4.md) ; configuration : `pat-19-campaign-v4.json` ; boucle opérateur : [`pat-19-v4-operator.md`](pat-19-v4-operator.md). Même schéma que la v2 et la v3, mêmes modes, même code. Les quatre clés v4 ci-dessous sont **optionnelles, absentes des configurations v1, v2 et v3 et refusées au chargement hors du protocole `pat-19-protocol-v4`** : le comportement de ces versions est inchangé (l'appel du juge, le texte de correction, l'agencement des dossiers et l'audit sont identiques).

- **`correction_feedback`** (`hidden_test_failures` booléen ; `max_failures`, `max_message_chars`, `max_name_chars` entiers positifs si vrai) : après un verdict `REFUSED` du juge, le texte de correction ajoute `Failing hidden tests (N shown of M):` puis une ligne `- <classname>::<nom coupé à max_name_chars>: <message>` par test échoué ou en erreur du rapport junit du juge, au plus `max_failures` lignes, chaque message aux espaces réduits et coupé à `max_message_chars` caractères, le chemin du bundle remplacé par `<bundle>` et le dossier temporaire du juge par `<tmp>` (tout autre chemin dans un message reste visible). Jamais le code source d'un test ; le module et le nom du test sont visibles (`classname::nom`) et un message peut contenir un chemin sous le bundle, seuls le chemin du bundle et le dossier temporaire du juge étant masqués. Le message junit est celui de pytest : il peut contenir l'expression d'une assertion **et les valeurs attendues** (`assert 0 == 5`, `+ where 0 = add(2, 3)`), donc un correcteur peut ajuster le code aux tests (même retour pour les deux bras) ; une erreur de collecte ne donne qu'un message de collecte (retour sans information) ; limites assumées. Un refus sans liste (délai, fichiers du harnais modifiés, défaut du candidat) n'ajoute rien. Même code pour tous les bras. Le juge ne renvoie la liste que si `lfc.judge(..., failures=True)` ; sans la clé, l'appel et le verdict sont ceux d'avant.
- **Compteurs d'enregistrement** : quand le retour est actif, l'enregistrement de chaque tour de correction suivant un refus du juge porte `feedback: {failing_shown, failing_total, collection_failure}` (nombre de tests montrés, nombre total échoués, présence d'un échec de collecte) : des compteurs, jamais un texte. Clé absente quand le retour est désactivé (enregistrements v1 à v3 inchangés), sur le tour d'implémentation et sur un tour de correction coupé par une erreur d'outil (enregistrement d'erreur).
- **`isolation.private_attempt_root`** (booléen) : voir « Audit de contamination ». Sous cette clé, le texte libre d'un rapport d'explorateur rendu dans l'énoncé de L est aussi masqué (chemin du bundle de l'explorateur → chemin relatif, racine de travail → `<work-root>`) : sinon L serait relevé pour avoir lu un chemin cité par l'explorateur.
- **`exploration.fixed_candidate`** : le candidat est fixé par le protocole : `compare-exploration` n'exige aucun résultat de tamis et refuse un autre candidat, `screen-exploration` est refusé (code 2).
- **`exploration.comparison_task_group`** (`screening` ou `comparison`, défaut `comparison`) : groupe du manifeste dont `compare-exploration` tire ses tâches (la v4 rejoue les six tâches du tamis v3). Les enregistrements gardent l'ensemble de tâches `comparison`.
- **Chargeur** : `protocol` vaut `pat-19-protocol-v4` : sont épinglés le candidat unique, les bornes d'exploration de la v3, `max_correction_rounds` 2, `one_task_per_launch`, le groupe `screening`, `correction_feedback` (20 noms, 300 caractères par message, 200 caractères par nom) et la racine privée ; un `protocol` v4 avec le schéma v1 est refusé.
- **Bras** : sous ce protocole, `compare-exploration` refuse tout bras autre que `A` et `L` avant toute réservation (code 2), et `--paths` vaut `A,L` par défaut. `report` d'un candidat fixé affiche `exploration_screening.state: no_screening` et `screening_selected: fixed_by_protocol:<candidat>`, jamais `incomplete_screening`.
- **Opérateur** : la ligne de fin de lancement reste `pat19-v3: work_remains=…`.
- **Statut documentaire (R5)** : ce document, le protocole v4 (section 6) et le CHANGELOG. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
- **Résultats réels (PAT-122)** : comparaison `pat-19-x4compare-1`, exécutée le 2026-10-07 : [`pat-19-exploration-results-v4.md`](pat-19-exploration-results-v4.md) (pièces brutes dans `pat-19-runs/x4compare-1/`). Le diagnostic qui y figure nomme trois défauts de l'audit de contamination (jetons relatifs résolus contre le bundle sans suivre un `cd`, dossier de travail réinitialisé à chaque appel Bash, imputation à la tentative entière d'une contamination du relecteur) : documentés ici comme constats de ce diagnostic. **Corrigés ensuite pour une campagne future** par PAT-123 (section « Révision 2 de l'audit et rejeu hors ligne » ci-dessous), jamais pour la v4.

## Révision 2 de l'audit et rejeu hors ligne (PAT-123)

Les protocoles v1 à v4, leurs configurations et leurs résultats sont gelés et **ne changent pas** : sans la clé ci-dessous, le
lanceur se comporte exactement comme avant (audit de révision 1, capture du correctif inchangée, enregistrements de même forme).
La réparation est une **révision 2** de l'audit, activée par une clé de configuration et donc par un protocole postérieur à la v4.

**Modèle de menace (décision du mainteneur, 2026-10-08).** L'audit lexical est un **journal** des sorties accidentelles d'un
bras qui fait son travail ; il n'est **pas une barrière** contre un bras qui chercherait à le tromper. La barrière est le bac
à sable natif de l'outil Bash de Claude Code, appliqué par le système d'exploitation (PAT-124). Tout ce que cette section
affirme de la révision 2 vaut **pour un bras qui ne cherche pas à tromper l'audit, sous les hypothèses H1 à H9**, et rien
au-delà.

- **Ce que les quatre essais du 2026-10-08 ont montré de la barrière** (rapportés par PAT-124, pas rejoués ici ; **T1** =
  essai manuel du coordinateur, **T2**, **T3**, **T4** = essais du lanceur, définis dans « Bac à sable natif des bras cloud
  (PAT-124) », paragraphe « Quatre essais du 2026-10-08 » ; T1 n'a ni les réglages ni le mode de permission du lanceur) :
  - **T2, essai du lanceur (résultat versé, mode `dontAsk`, Claude Code 2.1.285)** : session authentifiée (OAuth non
    affecté) ; `pytest --collect-only` (collecte seulement, **aucun test exécuté**) et `git add`/`git commit` fonctionnent
    dans le bundle avec un `allowRead` absolu du dossier de l'essai et `GIT_CONFIG_GLOBAL=/dev/null` ; la lecture par le
    shell d'un autre essai et d'un fichier du home est refusée par le système (`Operation not permitted`) ; l'outil Read hors
    de l'essai est refusé par `permissions.blockReadsOutsideWorkingDirectories`, et sur le home par une règle de permission ;
    l'écriture hors de l'essai, au shell et par l'outil Write, est refusée **par le mode `dontAsk`**, pas par le système.
  - **T1 seulement, essai manuel (preuves non versées, pas les réglages du lanceur : `bypassPermissions`, réglages écrits à
    la main)** : écritures par le shell dans un dossier voisin refusées par le système (« operation not permitted ») ;
    lecture par le shell du dossier parent refusée ; et le **trou** : l'outil Write crée un fichier dans un dossier voisin.
    La lecture du parent et le trou ne reposent que sur T1.
  - **T3 et T4, essais du lanceur avec le code durci (résultat de T4 versé)** : **un refus d'écriture par le système a
    maintenant été observé avec les réglages du lanceur** : la sonde P16 (un programme `python3` qui écrit hors de l'essai)
    est refusée par le système (`Operation not permitted`) dans T3 et dans T4. De plus : l'outil Write (P14, règle de
    permission) et le shell (P15, mode `dontAsk`) sont refusés sur le `.claude` du bundle ; un fichier de tests est exécuté
    et passe sous le bac à sable (T4 seulement : 6 tests d'un petit fichier qui ne fait que lire des fichiers du dépôt, pas
    la suite du bundle).
  - **Le trou de T1** est traité par PAT-124 (mode `dontAsk` et règles de permission) ; T2, T3 et T4 ont vu l'outil Write
    refusé hors de l'essai (par `dontAsk`), voir « Essai réel du 2026-10-08 ».
- **Couplage obligatoire (livré par PAT-124).** La révision 2 n'est acceptée **qu'avec** la clé
  `isolation.cloud_native_sandbox` (section « Bac à sable natif des bras cloud (PAT-124) » ci-dessous). Le lanceur ne voit
  jamais le système d'exploitation appliquer ce bac à sable : `audit.barrier` dit **ce qu'il a vérifié** (réglages transmis,
  version de Claude Code observée dans le flux), jamais « confiné ». PAT-123 avait posé `not_verified` constant faute de clé ;
  cette valeur reste celle d'un enregistrement sans bac à sable natif ou sans rien observé.
- **Limites connues : contournement délibéré, hors périmètre de PAT-123.** Listées, pas corrigées une à une (les mêmes, L1 à
  L6, en tête du bloc « audit revision 2 » de `local_first_runner.py`) :
  1. **L1, dossier ou lien déplacé sous le shell** : renommer, déplacer, remplacer ou supprimer un dossier ou un lien
     symbolique du chemin d'un candidat après y être entré (`mkdir d; cd d; mv ../d ../../d2; cat ../../../tests/…` ;
     `ln -s ../scratch l; cd l; rm ../bundle/l; cat ../../../tests/…`). Un programme déplace ainsi le shell sans `cd` ;
     l'audit, qui lit des noms, résout encore depuis l'ancien nom et **ne relève rien**. Deux tests
     (`…_is_a_known_limit_the_audit_does_not_see`) fixent ce comportement d'aujourd'hui pour qu'un changement se voie : ce
     ne sont pas des garanties.
  2. **L2, `CDPATH` ou autre état lu par `cd` réglé par un nom construit ou une évaluation arithmétique** (`print $O NOM …`
     avec `O=-v`, `let E` ou `typeset -i X=E` avec `E='CDPATH=5'`, `(( ))`). Durci seulement là où cela coûte une ligne et
     n'ajoute aucun faux drapeau sur un travail ordinaire : `print -v` (comme `printf -v`) et `integer`/`float` avec un
     argument contenant `$` retirent la confiance dans le `cd` qui suit ; `let` avec un tel argument fait abandonner le
     dossier (deux tests nommés). Le reste de la classe n'est pas traité.
  3. **L3, profil de l'hôte** : alias, fonctions, crochets (`chpwd`, `precmd`), modules et options du shell (`autocd`,
     `cdablevars`, `chaselinks`). H4 suppose qu'il n'y en a pas ; rien dans le flux ne le montre.
  4. **L4, commande construite à l'exécution** (nom ou script que le shell développe ou lit dans un fichier). L'audit
     abandonne le dossier pour les formes qu'il lit (`$X ..`, `eval`, `source`) ; il ne voit pas les autres.
  5. **L5, `cd` caché dans un script ou un interpréteur** (`python3 -c "os.chdir('..'); …"`, `make -C`, `git -C`, un fichier
     de script écrit par le bras) : le dossier de ce programme n'est pas suivi (H8).
  6. **L6, chemin construit à l'exécution** ou gardé dans un fichier pour un appel suivant (H8, comme en révision 1).

  **L5 et L6 ne sont pas des évasions délibérées** : ce sont des angles morts hérités de la révision 1 que rencontre le
  **travail ordinaire** (`git -C <dossier>`, `make -C <dossier>`, un script que le bras a écrit, un chemin assemblé à
  l'exécution). La **cible** d'un `-C` reste un chemin lu par l'audit, donc **toujours relevée** si elle sort de la zone
  (testé : `git -C ../../../` et `make -C ../../../tests` sont relevés ; `git -C ../../b` dans la zone ne l'est pas).
- **Faux drapeaux du travail ordinaire, coûts connus à porter au ticket v5** (PAT-124, remarque N3 de la quatrième revue) :
  la révision 2 abandonne le dossier (`UNKNOWN_CWD`, donc le chemin relatif suivant du même appel est relevé) pour
  `source .venv/bin/activate`, `break` et `continue`, `trap`, `cd "$(git rev-parse --show-toplevel)"`, et un mot `cd` dans un
  message de validation ou un document en ligne. Mesuré par six tests nommés (la révision 1 n'en relève aucun).
  `break`/`continue` ne déplacent pas le shell, mais ne sont **pas** rendus inertes : terminer une boucle plus tôt est ce que
  la lecture de boucle de l'audit ne modélise pas, et le changement n'est pas « clairement sûr » dans les deux shells ; il
  reste un coût connu. **S'y ajoute, sous le bac à sable natif, l'avertissement `git` sur `~/.config/git/ignore`** (« unable to
  access … Operation not permitted », le fichier d'exclusion par défaut sous le home étant refusé au shell) : son écho dans un
  résultat d'outil est relevé comme chemin du home. Compte observé : **1 drapeau pour le relecteur dans chacun des trois
  essais du lanceur** (T2, T3, T4), alors que le relecteur n'a rien lu hors de son essai ; l'avertissement figure aussi
  dans 2 résultats de la sonde de chaque essai. À porter au ticket v5 (rediriger `XDG_CONFIG_HOME` ou le traiter dans
  l'audit) : sous la révision 2 un relecteur relevé laisse la tentative indécise, et il l'a été dans les trois essais.

- **`isolation.audit_revision`** (entier, 1 par défaut ou 2). Liste blanche : le chargeur n'accepte 2 que sous un `protocol` de la
  forme `pat-19-protocol-vN` avec N >= 5 ; une valeur autre que 1 ou 2, un protocole v1 à v4, absent ou inconnu est refusé.
  **Limite à lever par un protocole v5 (à porter dans le ticket v5)** : les clés v4 (`correction_feedback`,
  `isolation.private_attempt_root`, `exploration.fixed_candidate`, `exploration.comparison_task_group`) ne sont acceptées
  que sous `pat-19-protocol-v4`, où la révision 2 est refusée : **aujourd'hui aucune configuration ne peut charger à la fois
  les clés v4 et la révision 2**. Un v5 qui veut les deux demande un changement du chargeur ; les tests de la révision 2 sur
  bras factices construisent leur campagne sans passer par le chargeur.
- **Dossier courant (le cœur de la révision 2).** Un chemin relatif d'une commande est résolu depuis le dossier où la
  commande s'exécute. L'audit ne connaît jamais ce dossier : il tient un **ensemble de répertoires candidats** et relève un chemin
  dès qu'**un** candidat le fait sortir de la zone permise. Toute la règle tient en un invariant : **l'ensemble des candidats
  contient le dossier réel**. Un `cd` **remplace** l'ensemble (seul geste par lequel la révision 2 peut relever moins que la
  révision 1) dans le cas ci-dessous et nulle part ailleurs ; tout le reste ne fait qu'**ajouter** des candidats. La règle n'est
  plus une liste de formes dangereuses : c'est une **liste blanche**.
  - **Grammaire de confiance** (`_simple_script`) : le script de l'appel, corps de documents ici retirés, est une suite de
    chaînes séparées par `;` ou un retour à la ligne ; une chaîne est faite de tubes reliés par `&&` ou `||` ; un tube, de
    commandes simples reliées par `|` ; une commande simple, d'affectations, de mots et de redirections ; un mot, de caractères
    ordinaires, de guillemets et de `$NOM`/`${NOM}`. Rien d'autre : ni
    sous-shell, ni groupe, ni substitution, ni accent grave, ni `&`, ni mot réservé (`if`, `for`, `while`, `{`, `!`, `[[`,
    `time`…), ni définition de fonction, ni commande vide.
  - **Quand un `cd` remplace les candidats** (`_walk_simple`, les six conditions à la fois) : (1) le script est dans la
    grammaire ; (2) le résultat de l'appel est **propre** et **entier** (H2, H3 : le script a rendu 0, donc il est allé au
    bout) ; (3) aucune commande avant le `cd` n'a pu finir le script avec le statut 0 (`exit`, `return`, `exec`, `logout`,
    `bye`), déplacer le shell hors de vue ou changer ce que lit `cd` (toute commande interne hors de la liste blanche des
    commandes inertes, un nom de commande que le shell construit, `CDPATH`) ; (4) le `cd` est le premier tube de sa chaîne,
    seul dans son tube, sans affectation ni redirection, et aucun `||` ne le suit dans la chaîne ; (5) il a une seule cible, un
    mot que le shell ne développe pas (ni `$`, ni joker, ni accolade, ni `~`), qui ne commence ni par `-` ni par `+`, après
    les options `-L`, `-P`, `-e`, `-q`, `-s`, `--` ; (6) le résultat ne contient
    **aucune ligne `cd:`**, quel que soit le message (H6). La cible est résolue des deux façons, logique et physique (lien
    symbolique traversé, `cd -P`) : les deux répertoires sont candidats.
  - **Partout ailleurs le `cd` n'est pas cru** : il **ajoute** sa cible aux candidats, ou `UNKNOWN_CWD` si la cible n'est pas
    lisible (`cd $X`, `cd -`, `cd` seul, joker, deux cibles, affectation devant) ; `UNKNOWN_CWD` fait de tout chemin relatif
    un drapeau (`/<unknown-working-directory>`) et s'ajoute aussi pour toute commande interne hors liste blanche (`pushd`,
    `popd`, `eval`, `source`, `.`, `trap`, `alias`, `setopt`, `builtin cd`…), pour un nom de commande construit (`$X ..`) et
    pour `CDPATH`. Hors de la grammaire (`_walk_loose` : boucle, fonction, sous-shell, `if`, substitution…), **aucun ordre
    n'est cru** : chaque `cd` lisible a pu s'exécuter, une fois et dans l'ordre du texte s'il n'y a ni boucle ni fonction,
    **un nombre quelconque de fois dans un ordre quelconque sinon** (point fixe ; au-delà de 32 candidats, `UNKNOWN_CWD`), et
    chaque chemin relatif du script est résolu contre **tous** les candidats du script.
  - **Filet des jetons** : tout chemin relatif d'un appel est résolu contre tous les répertoires où le shell a pu être
    **depuis la commande qui le nomme jusqu'à la fin de l'appel**, shells enfants compris (`X=../x; cd ..; cat $X`,
    `bash -c 'cat "$1"' _ ../x`, arguments d'une fonction, liste d'un `for`) ; seuls les mots d'un `cd` sont résolus là où
    il se trouve. **Toute autre commande nomme aussi `.`, le dossier où elle s'exécute** : un nom nu (`cat x`, `ls`) n'est pas
    un jeton de chemin, donc une commande qui a pu s'exécuter hors de la zone permise est un drapeau par elle-même (rôle que
    joue en révision 1 la cible relevée du `cd` qui y a mené), y compris quand cette cible n'était pas lisible
    (`cd $X; cat y` : relevé ici, pas en révision 1). Un mot `cd`/`chdir`/`pushd`/`popd` du texte que l'audit n'a pas lu comme une commande (dans une chaîne,
    `x=cd; $x ..`) ajoute `UNKNOWN_CWD` : il a pu être exécuté hors de vue.
  - **D'un appel au suivant** (`_next_session`, hôte observé seulement) : après un résultat propre, les candidats de fin ;
    après tout autre résultat (erreur, statut non nul accepté par l'hôte, interruption, arrière-plan, résultat absent, trop
    long ou tronqué, script contenant `exit`/`return`/`exec`), **tous** les répertoires où l'appel a pu être, son départ compris.
    `Shell cwd was reset to …` dans le résultat décide : la racine du bundle (le répertoire le moins profond, donc une ligne
    contrefaite ne peut que durcir). Un candidat hors projet **sans** cette ligne est gardé, avec la racine. Deux appels
    envoyés sans attendre le résultat du premier (ordre d'exécution inconnu ; aucun dans les flux v4) ne prouvent rien.
  - **Hypothèses sous lesquelles « candidats ⊇ dossier réel » tient, pour un bras qui ne cherche pas à tromper l'audit**
    (les mêmes, numérotées H1 à H9, en tête du bloc « audit revision 2 » de `local_first_runner.py` ; hors de ce cadre,
    voir les limites connues L1 à L6 ci-dessus) :
    1. **H1, hôte** : le flux est un flux Claude Code dont tous les événements `init` nomment une version observée
       (`OBSERVED_CLAUDE_CODE` : `2.1.285`). Observé dans les 30 flux cloud de la v4 : chaque appel Bash est un nouveau shell
       qui lance la commande par `eval` (les erreurs zsh s'écrivent `(eval):cd:1: …`) ; le dossier de fin d'un appel est repris
       par le suivant quand l'appel a rendu 0 et que le dossier est dans le projet ; un appel qui finit hors projet est remis
       à la racine avec « Shell cwd was reset to … ». **Non établi** : si le dossier est repris après un appel qui a rendu un
       statut non nul (dans un flux v4, un appel de statut 1 qui commençait par un `cd` a laissé l'appel suivant à la racine,
       sans qu'on sache si ce `cd` s'était exécuté) ; l'audit garde donc les deux. Pas de sous-agent : le lanceur refuse un
       flux cloud dont l'`init` liste un autre outil que Bash, Edit, Read, Write.
    2. **H2, résultat** : un résultat sans `is_error`, avec l'objet de détail de l'hôte sans `interrupted`,
       `returnCodeInterpretation` (statut non nul que l'hôte accepte, `grep` 1 par exemple) ni tâche d'arrière-plan, signifie
       que le script a rendu 0 (« propre »). Tout autre résultat, et un résultat absent, ne prouve rien.
    3. **H3, sortie** : un résultat d'au plus 20 000 caractères sans marque de troncature contient toute la sortie standard
       et d'erreur. Aucun résultat tronqué dans les flux v4 ; les marques sont celles de l'hôte telles que connues, non observées.
    4. **H4, shell** : bash ou zsh avec ses options par défaut pour `cd` (ni `autocd`, ni `cdablevars`, ni `chaselinks`), sans
       `CDPATH`, sans alias, fonction, crochet (`chpwd`) ni module qui déplace ou termine le shell sous un nom hors de
       `_SHELL_NAMES`. Un nom de commande hors de `_SHELL_NAMES` est un programme : il ne peut pas faire exécuter un `cd`
       au shell, mais il **peut** le déplacer sans `cd`, en renommant, déplaçant ou remplaçant un dossier ou un lien
       symbolique du chemin où le shell se trouve (limite L1) ; l'audit suppose que le bras ne le fait pas et ne le
       vérifie pas. Hors dossier courant, l'état du shell ne passe pas d'un appel au suivant. **Rien dans le flux ne le
       montre : supposé.**
    5. **H5, statut** : un script qui s'arrête avant sa fin rend un statut non nul, sauf `exit`, `return`, `exec`, `logout`,
       `bye` (lus par l'audit, qui ne croit alors ni `cd` suivant ni dossier de fin). Observé sous bash 3.2 et zsh 5.9 pour les
       formes engendrées par le test, et une fois sur l'hôte (un « no matches found » de zsh a donné « Exit code 1 ») ; non
       prouvé pour toute erreur de shell.
    6. **H6, `cd`** : un `cd` raté écrit une ligne contenant `cd:` sur la sortie d'erreur du shell.
    7. **H7, lecture** : les lecteurs de l'audit découpent le script comme le shell. Gardes : le filet des mots `cd` ci-dessus,
       `$'…'` et un document ici jamais fermé retirent toute confiance.
    8. **H8, usage** : un chemin relatif nommé dans un appel est utilisé, s'il l'est, par la commande qui le nomme ou une
       suivante du **même** appel, par un programme qui le résout depuis le dossier du shell (pas `git -C`, `make -C`,
       `os.chdir`) ; un chemin construit à l'exécution n'est pas vu, comme en révision 1.
    9. **H9, sinon** : si H1 ne tient pas (flux omp, autre version de Claude Code, version absente), **repli plus strict, pas
       silence** : aucun dossier n'est repris, chaque appel part de l'**union** de la racine et de tout répertoire où un appel
       précédent a pu être, et aucun `cd` ne remplace rien. L'enregistrement le dit (`audit.host_models` : `unverified` au
       lieu de `claude-code-2.1.285`). À revérifier, et la liste `OBSERVED_CLAUDE_CODE` à compléter, à chaque version d'hôte.
  - **Où la révision 2 relève moins que la révision 1** : seulement quand un `cd` est cru (conditions ci-dessus) et que le
    chemin, résolu depuis ce dossier, reste dans la zone : c'est la correction voulue (les faux drapeaux de la v4). Cela
    vaut sous H1 à H8 pour un bras qui ne cherche pas à tromper l'audit, et pas au-delà (limites L1 à L6) ; un chemin inexistant (ci-dessous) n'est pas une lecture. Le coût inverse est
    un **sur-relevé** assumé dans les cas ambigus : un `cd` suivi d'une commande qui échoue, d'un `grep` sans résultat ou
    d'un `||`, un `cd` dans une boucle ou un sous-shell, un mot `cd` dans un message, laissent plusieurs candidats.
  - **Répertoire `mktemp -d` : non modélisé (décision, avec sa raison).** `T=$(mktemp -d); cd $T/…` reste un `cd` de cible
    illisible : `UNKNOWN_CWD`, donc la commande suivante est relevée. Modéliser ce répertoire comme un lieu connu et permis
    (il vient d'être créé : ni une autre tentative, ni les tests cachés, ni le répertoire personnel) a été écrit, testé, puis
    **retiré**, parce qu'on ne peut pas le rendre sûr : l'audit ne connaît pas le chemin réel du répertoire et ne peut donc
    pas, comme il le fait pour le bundle, y résoudre les liens symboliques ; un lien que le bras y pose
    (`ln -s / $T/r; cd $T/r`, ou une arborescence copiée qui en contient) mènerait hors zone sans être vu. Les conditions sur
    la liaison elle-même (affectation unique et inconditionnelle, pas de gabarit, de `-p`, de `TMPDIR`, de `PATH=`, pas de
    `mktemp` raté) étaient tenables ; celle sur le contenu ne l'est pas. Coût : un relecteur qui travaille dans une copie
    `mktemp` est relevé (deux sessions de la v4). Suite possible, à passer par `foundry:intake` : demander au relecteur de
    travailler dans son dossier d'essai (`scratch/`, zone permise où les liens se résolvent) plutôt que dans `mktemp`.
  - **Preuves** : un test nommé par cas de la seconde revue et ses variantes (arrêt avant le `cd` : `set -e`, `exit`,
    `return`, `exec`, erreur d'expansion, statut non nul, délai, arrière-plan, résultat absent ou tronqué ; `cd` répété :
    `for`, `while`, `until`, fonction appelée deux fois, fonctions imbriquées ; arguments positionnels : `sh -c`, `xargs`,
    `find -exec`, fonction, `for`, `set --`, affectation) ; commande exécutée hors zone ; les tables « cru » / « pas cru » ;
    la liste `_SHELL_NAMES`
    comparée aux commandes internes des shells installés ; et un **test par propriété** contre un vrai `/bin/bash` et un vrai
    `zsh` (le shell de l'hôte), 150 suites d'appels engendrées pour chacune des 8 combinaisons shell × comportement d'hôte :
    le dossier réel de départ de chaque appel est dans les candidats et tout chemin réellement résolu hors zone est relevé.
    **Observé** par l'oracle : ce que le shell exécute, où, avec quel statut et quels messages. **Encodé** (aucun hôte n'est
    lancé) : `eval`, la lecture du dossier de fin (à la sortie du shell, ou après le script), la reprise ou non après un
    statut non nul, la remise à la racine avec sa ligne, la forme du résultat. Non couvert par
    l'oracle : `trap`, les alias et options du profil de l'hôte (H4), et tout contournement délibéré (L1 à L6 : l'oracle
    engendre des formes de travail ordinaire, pas des manœuvres). **Shell couvert** : le shell de l'hôte est **zsh** ; sur
    une machine sans zsh (une image de CI peut n'avoir que bash), la moitié zsh du test par propriété et du test de
    `_SHELL_NAMES` est **sautée de façon visible** (`skipped` dans le rapport de tests, avec sa raison), donc non couverte
    là : seule une exécution locale avec zsh la prouve. Aussi couverts par des tests nommés : les deux durcissements de L2,
    les deux limites L1 (comportement d'aujourd'hui), et une coupure pendant l'implémenteur relevé (refus qui porte l'audit
    de la session, interruption) : l'essai du bras est `contaminated`, chemins nommés une seule fois, sous les deux révisions.
- **Chemin inexistant** : un appel d'outil omp qui a donné un `path` et dont le résultat est une erreur `Path not found: <ce
  même chemin>` n'a rien lu : ni le chemin ni l'écho de l'erreur ne sont des drapeaux ; le chemin est consigné dans
  `audit.not_found` de l'enregistrement. Un autre texte d'erreur, un résultat non erroné, un résultat d'un autre appel, un autre
  chemin nommé, une commande Bash (clé `command`) ou un second accès réussi au même chemin restent audités comme avant. Ce
  chemin peut être n'importe où (dans sa propre racine privée comme ailleurs) : `not_found` dit « le bras a nommé ce chemin »,
  non « le bras a visé la zone interdite ».
- **Session du relecteur** (chemin cloud) : un drapeau de la session du relecteur ne met plus `contaminated` sur l'essai du bras.
  Il est enregistré dans `review.contamination` (`paths`, `commands`) et `unknown["review.contaminated"]`, et les verdicts du
  juge et de la revue restent lisibles. **Un relecteur relevé ne décide rien, quel que soit son verdict** : l'issue est
  `review_unreadable` (indécidé, jamais rejoué), `accepted` reste `None`, la boucle s'arrête et ses conclusions ne sont
  transmises à aucun correcteur (un `BLOCK` d'un relecteur relevé n'alimente donc pas une correction). Le chemin C (v1) garde
  son comportement. **Même règle si l'essai est coupé** (panne d'outil, interruption) après l'audit du relecteur : le drapeau
  du relecteur va dans `review.contamination` de l'enregistrement coupé et ne le rend pas `contaminated` (sous la révision 1,
  il le rend `contaminated` comme avant). `report` liste ces enregistrements dans `review_contaminated` (clé présente
  seulement s'il y en a, donc jamais pour une campagne gelée), à côté de `contaminated`.
- **Capture du correctif** : `_PATCH_EXCLUDES_V2` ajoute `.pytest_cache/` et `.ruff_cache/` aux exclusions de `_capture_patch`.
  Origine établie : le bras lance `pytest` / `ruff` dans son bundle, ce qui crée ces dossiers avec leur propre `.gitignore` ; le
  `.gitignore` du dépôt ignore déjà `.pytest_cache/` ; `git add -A -f` de la capture force l'ajout des fichiers ignorés et les
  exclusions ne les couvraient pas : c'est l'**instrument** qui les a mis dans le correctif et dans le diff du relecteur. **Seuls
  ces deux caches** sont traités (ce sont les deux que montrent les flux v4) ; `-f` est gardé parce que l'enlever changerait ce
  que la capture garde au-delà de ces deux dossiers (tout autre fichier ignoré qu'un bras ajoute légitimement) et donc ce que
  voit le juge ; un autre cache (`.mypy_cache`, `.hypothesis`, `.coverage`) serait encore capturé jusqu'à ce qu'une campagne le
  montre. Les v1 à v4 gardent `_PATCH_EXCLUDES`.
- **Enregistrement** : sous la révision 2 seulement, chaque enregistrement d'essai **allé à son terme** porte `audit: {revision,
  barrier, not_found, host_models}` (un enregistrement coupé par une panne d'outil ou une interruption, `_tool_error`, n'en
  porte pas : il n'est jamais lisible comme vérifié) ; `barrier` dit ce que le lanceur a vérifié du bac à sable natif (valeurs
  dans la section PAT-124 ; `not_verified` sans bac à sable natif ou sans observation) ; `host_models` dit, par flux audité et dans l'ordre, ce que l'audit a pu supposer de l'hôte
  (`claude-code-2.1.285`, ou `unverified` : repli H9). Un enregistrement coupé (panne d'outil, interruption) ne porte pas ce
  champ, sous aucune révision.
- **`informative_arms`** (rapport d'exploration) ne nomme plus qu'un bras informatif **joué** (`["E"]` si l'arme E a des
  enregistrements, `[]` sinon). Ce correctif de sortie s'applique à tout rapport futur, v2 et v3 compris, et ne change aucune
  décision ni aucun verdict ; les rapports versés ne sont pas recalculés.
- **Rejeu hors ligne** : `python3 -m foundry.local_first_runner replay-audit --campaign <config> --results
  results-<id>.jsonl --streams-dir <flux bruts> --work-root <racine de travail> [--repo .] [--home <HOME>]
  [--work-root-not-sensitive] [--out <fichier>]` (le registre `ledger-<id>.jsonl` est lu à côté des résultats ; `--out`
  n'écrase jamais un fichier existant). Il applique à chaque flux les révisions 1 et 2, sans pilote, sans modèle, sans appel
  cloud, et rend par enregistrement ce qui a été enregistré, l'ancien classement, le nouveau, les chemins inexistants à part, le
  `sha256` du flux et une classification : `clean`, `flag_kept` (mêmes chemins), `flag_kept_changed` (gardé, chemins différents),
  `flag_removed`, `flag_moved_to_review` (le drapeau n'est plus dans la session du bras mais dans celle du relecteur),
  `flag_moved_to_not_found` (plus de lecture, un chemin inexistant consigné à part), `flag_added` (rien d'enregistré, un
  drapeau maintenant), `not_comparable` (la révision 1 rejouée ne retrouve pas ce qui a été enregistré), `unavailable` (un flux
  manque : jamais « propre »). Le résumé compte à part `flagged_now` (session du bras) et `reviewer_flagged_now`
  (enregistrements dont seule ou aussi la session du relecteur est relevée), et `host_models` (flux par modèle d'hôte) ;
  chaque flux porte son `host_model`. `--work-root-not-sensitive` rejoue sans la racine de travail dans la liste sensible : c'est la
  mesure de ce que le changement v4 explique (ses fidélités sont attendues « mismatch » : l'enregistré, lui, l'avait). Aucun
  verdict, issue ni rapport n'est recalculé. Limites : racines sensibles reconstruites avec le dépôt, le dossier d'état et le
  répertoire personnel du rejeu ; littéraux de base de l'extraction courante ; refus du bac à sable local non reconstruit ; **rôles** :
  la première session cloud d'un enregistrement est prise pour celle du bras et les suivantes pour celles du relecteur (vrai pour
  les chemins A et L ; faux pour le chemin C ou un enregistrement repris) ; un chemin sous le répertoire personnel est écrit
  `~/<hidden>` et le rejeu refuse de produire un résultat qui en copie un. Résultat sur la v4 :
  [`pat-19-audit-replay-v4.md`](pat-19-audit-replay-v4.md).
- **Statut documentaire (R5)** : ce document, le rejeu v4, le CHANGELOG ; artefacts : verbe `replay-audit` (dont
  `--work-root-not-sensitive`), clé `isolation.audit_revision`, constantes `AUDIT_REVISION`, `FROZEN_PROTOCOLS`,
  `UNKNOWN_CWD`, `OBSERVED_CLAUDE_CODE`, `AUDIT_BARRIER`, `_PATCH_EXCLUDES_V2`, paramètres `revision`, `not_found`,
  `host_model` et `trace` de `audit_transcript`, champs `audit` (dont `host_models` et `barrier`) et `review.contamination`, clé
  `review_contaminated` du rapport, compteurs `reviewer_flagged_now` et `host_models` du rejeu, `informative_arms`. Aucun verbe de `foundry_cli.py`, clé de configuration produit, table de routage ni
  constante de routage modifiés. Contrainte opérationnelle documentée ici : la révision 2 ne s'active qu'avec le bac à sable
  natif (PAT-124), non vérifié par le lanceur. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.

## Bac à sable natif des bras cloud (PAT-124)

**Rien ne change sans la clé.** Les protocoles v1 à v4, leurs configurations et leurs résultats sont gelés : sans
`isolation.cloud_native_sandbox`, la ligne de commande et l'environnement des pilotes cloud sont **octet pour octet** ceux de
la v4 (test sur la configuration v4). La clé est un booléen accepté **seulement** sous `pat-19-protocol-vN`, N >= 5 (liste
blanche comme `audit_revision`) : refusée sous v1 à v4, sans protocole ou sous un protocole inconnu. Au chargement :
`audit_revision: 2` n'est accepté qu'avec elle ; `isolation.private_attempt_root` est maintenant accepté sous un protocole v5
ou postérieur (les autres clés v4 `correction_feedback`, `exploration.fixed_candidate`, `exploration.comparison_task_group`
restaient v4 seulement dans PAT-124 ; **PAT-126 les accepte aussi sous un protocole postérieur à la v4**, voir « Protocole v5 (PAT-126) ») ; avec la clé, chaque pilote cloud
déclare `sandbox: false` (le processus `claude` n'est **jamais** enveloppé : R6, seul son shell est confiné par Claude Code)
et un flux `claude-stream-json`.

**Quatre essais du 2026-10-08, à ne jamais confondre.** Partout dans ce document et dans le CHANGELOG :

- **T1, essai manuel du coordinateur** : hors du lanceur, mode `bypassPermissions`, réglages écrits à la main, modèle
  `claude-haiku-4-5`, dossier jetable. **Ses preuves ne sont pas versées au dépôt et ce ne sont pas les réglages du
  lanceur.** Il a vu des écritures du shell dans un dossier voisin refusées par le système (« operation not permitted ») et
  l'outil Write créer un fichier dans un dossier voisin. Une observation qui ne repose que sur T1 est dite « T1 seulement ».
- **T2, essai du lanceur** : verbe `native-sandbox-trial`, tâche PR 27, deux exécutions cloud, mode `dontAsk`, réglages
  calculés par le lanceur. **Son résultat est le fichier versé**
  [`pat-19-native-sandbox-trial-2026-10-08.json`](pat-19-native-sandbox-trial-2026-10-08.json). Sa sonde P9 (écriture du
  shell hors de l'essai) a été refusée par le mode `dontAsk`, **pas** par le système. Il précède le durcissement de la
  revue 1 ; P2 n'y était qu'une collecte (`pytest --collect-only`) et P14 à P16 n'existaient pas.
- **T3, essai du lanceur avec le code durci** (commit `7dddce7`) : même verbe, tâche PR 27, deux exécutions cloud, mode
  `dontAsk`, `--probe-test plugins/foundry/tests/test_benchmark_evidence.py`. **Son résultat n'est pas versé** (ses chiffres
  sont dans « Essai réel du 2026-10-08 »). Toutes les sondes ont donné le même résultat que dans T4, **sauf P2, restée
  `unknown`** : pytest a répondu « 3 deselected », parce que ce fichier figure, à cette base, dans la liste des tests
  `benchmark_campaign` que `tests/conftest.py` n'exécute que sur demande ; pytest a démarré et n'a rien sélectionné. C'est
  une erreur de choix du fichier par le coordinateur, **pas un effet du bac à sable**.
- **T4, essai du lanceur avec le code durci** (commit `7dddce7`) : identique à T3 avec `--probe-test
  plugins/foundry/tests/test_process_contract.py`. **Son résultat est le second fichier versé**
  [`pat-19-native-sandbox-trial-2026-10-08-t4.json`](pat-19-native-sandbox-trial-2026-10-08-t4.json),
  produit par la réévaluation hors ligne. P2 : « 6 passed ». P16 (un programme écrit hors de l'essai) : refusée **par le
  système**. P14 et P15 (écriture dans le `.claude` du bundle) : refusées, par une règle de permission et par `dontAsk`.

Ce qui est dit « observé avec les réglages du lanceur » repose sur T2, T3 ou T4, jamais sur T1.

**Ce que le lanceur passe, par exécution** (`native_sandbox_settings`, `with_native_sandbox`, chemins **absolus** résolus pour
l'essai en cours : `"."` ne désigne pas le dossier courant dans `--settings`) : `--permission-mode dontAsk` (remplace le
`bypassPermissions` du pilote) et `--settings <json>` ajoutés **à la fin** de la ligne de commande (un drapeau qui en suit un
autre termine une option variadique comme `--disallowedTools` : rien du pilote n'est avalé ni déplacé). Exemple, attempt
`/srv/pat19/work/private-a/a`, home `/home/maint` (la forme exacte est `json.dumps(..., sort_keys=True)` compact) :

```json
{"permissions": {"additionalDirectories": ["/srv/pat19/work/private-a/a"],
  "allow": ["Read(//srv/pat19/work/private-a/a/**)", "Edit(//srv/pat19/work/private-a/a/**)"],
  "blockReadsOutsideWorkingDirectories": true,
  "deny": ["Read(//home/maint/**)", "Edit(//home/maint/**)", "Read(//home/maint/.ssh/**)", "Edit(//home/maint/.ssh/**)",
           "Read(//srv/dev/repo/**)", "Edit(//srv/dev/repo/**)"]},
 "sandbox": {"allowUnsandboxedCommands": false, "autoAllowBashIfSandboxed": true, "enabled": true,
  "failIfUnavailable": true,
  "filesystem": {"allowRead": ["/srv/pat19/work/private-a/a"], "allowWrite": ["/srv/pat19/work/private-a/a"],
                 "denyRead": ["/home/maint", "/srv/pat19/work", "/srv/dev/repo"]}}}
```

- **Shell (système d'exploitation)** : lecture refusée sur le home et sur la racine de travail, **réautorisée sur le dossier
  de l'essai** (bundle et scratch ; la règle au chemin le plus étroit l'emporte) et sur `isolation.allow_read_home` (relatif au
  home, vide par défaut : c'est l'endroit où déclarer ce dont le pilote a besoin, par exemple l'interpréteur d'un environnement
  sous le home) ; écriture dans le dossier de l'essai, `extra_write` du pilote et le dossier temporaire par utilisateur
  (valeur par défaut de Claude Code). Les chemins refusés de la liste cloud (`deny_read`, dont le dépôt de développement et le
  dossier d'état) s'ajoutent s'ils ne sont ni sous le home ni sous la racine de travail.
- **Environnement** : `GIT_CONFIG_GLOBAL=/dev/null` et `GIT_CONFIG_NOSYSTEM=1` sont **ajoutés** à l'environnement du fils
  **seulement sous cette clé**, après `env_set`. Les variables de l'allow-list R6 (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`,
  `PATH`, `TMPDIR`, `USER`) sont intactes ; `FOUNDRY_DATA` (PAT-120) aussi.
- **Outils de fichiers (Read, Edit, Write, Glob, Grep)** : ils sont **hors** du bac à sable (documentation Claude Code,
  « Sandboxing » : « Claude's file tools … run outside it ») et suivent les règles de permission. Le trou observé par T1
  seulement (l'outil Write créait un fichier dans un dossier voisin sous `bypassPermissions`, qui ne demande rien ; preuve
  non versée, pas les réglages du lanceur) est traité par le
  **mode `dontAsk`** (documentation « Permission modes » : tout appel qui demanderait une approbation est **refusé**, sans
  jamais attendre, adapté à `claude -p`) combiné à : `blockReadsOutsideWorkingDirectories` (une lecture hors des dossiers de
  travail demande, donc est refusée) ; `additionalDirectories` = le dossier de l'essai (le scratch est un frère du bundle et
  doit rester lisible : retour du correcteur, `review.json`) ; des règles `allow` `Read`/`Edit` limitées au dossier de l'essai
  (une règle `Edit` couvre aussi Write ; une règle `Read` couvre Glob et Grep : la documentation précise que Claude Code ne
  consulte que les règles `Read(...)` et `Edit(...)`, pas `Write(...)` ni `Glob(...)`) ; des règles `deny` `Read`/`Edit` sur
  le home et sur les chemins sensibles qui ne **contiennent pas** le dossier de l'essai (un refus l'emporte sur un
  `allow` : si la racine de travail est sous le home, la règle de permission du home est omise ; la règle `denyRead`/`allowRead`
  du système d'exploitation, à chemin plus étroit, reste, mais **elle ne s'applique pas aux outils de fichiers** : dans cette
  branche, Read sur le reste du home ne tient que par `blockReadsOutsideWorkingDirectories`, Write et Edit que par `dontAsk`.
  Cette branche n'a **pas été jouée** : la racine de travail de l'essai était hors du home). **Source** : <https://code.claude.com/docs/en/permissions> (Read and Edit),
  <https://code.claude.com/docs/en/permission-modes> (dontAsk, bypassPermissions : « Allow rules have no effect in
  bypassPermissions »), <https://code.claude.com/docs/en/sandboxing>. **Établi par les essais du lanceur (T2, T3, T4)** : `dontAsk` avec
  `autoAllowBashIfSandboxed` laisse le shell travailler dans le bundle (`git` ; collecte `pytest --collect-only` dans T2 ;
  un fichier de 6 tests exécuté et réussi dans T4, petit fichier en lecture seule, pas la suite du bundle), Read et Write
  hors de l'essai sont refusés ; T3 et T4 : Write refusé sur le `.claude` du bundle par la règle de permission (P14).
  **Non établi** : Glob et Grep (absents de ce pilote, la documentation dit que la règle `Read` ne s'y applique qu'« au
  mieux ») ; la règle de permission d'Edit hors de l'essai (l'outil exige une lecture préalable, refusée : non exercée).
- **Relecteur** : même zone que l'implémenteur, pour les outils de fichiers : son dossier d'essai (bundle et scratch). Pour le
  shell, s'y ajoute le dossier temporaire par utilisateur, écrivable par défaut : la copie `mktemp` qu'il faisait (observée dans
  les flux v4) **devrait y fonctionner au shell (non exercé)**, et pas aux outils Read/Edit/Write (hors des dossiers de
  travail) : c'est une déduction de la documentation, aucun relecteur de T2, T3 ou T4 n'a fait de copie `mktemp`
  (`reviewer.mktemp_used` faux). Les instructions du relecteur ne sont **pas** changées ; les trois essais du lanceur n'ont
  pas montré qu'il lui faille les outils sur une copie (verdict lu à chaque fois) ; si un essai ultérieur le montrait, ce
  serait un nouveau texte de protocole non gelé pour un protocole futur, jamais l'édition d'un texte gelé.
- **`audit.barrier`** (enregistrement allé à son terme, révision 2) : `not_verified` (pas de bac à sable natif, ou rien
  observé, ou le flux montre un autre mode de permission que celui demandé) < `settings_transmitted` (`--settings` passé à un
  processus qui a démarré) < `settings_transmitted_version_observed` (de plus : le flux nomme **une** version de Claude Code
  dans son événement `init`, et se termine par un `result` qui n'est pas une erreur). Pour un enregistrement à plusieurs
  flux, le plus faible. **Aucune valeur ne dit « confiné »** : le lanceur n'observe pas le système d'exploitation appliquer
  la barrière. Un enregistrement coupé n'a pas de champ `audit` (N1 : « tout enregistrement porte `audit.barrier` » était trop
  large ; c'est « un enregistrement allé à son terme »).
- **Limites restantes** : le bac à sable ne couvre que le shell ; les outils de fichiers reposent sur les permissions
  (documentées « au mieux » pour Glob et Grep) ; le réseau reste ouvert (aucun `network.allowedDomains` n'est passé) et
  le trousseau, `gh` et les identifiants implicites ne sont pas retirés par ce ticket (les règles `Bash(gh:*)`, etc. de
  `cloud_bash_deny` restent le seul frein au mieux, voir « Exposition des bras cloud ») ; `ls /` liste encore la racine du disque (observé par T2 :
  17 noms, sans pouvoir entrer dans le home ni dans la racine de travail) ; `denyRead` ne contient que le home, la racine de
  travail et les chemins refusés de la liste cloud, donc **restent lisibles** (non sondés sauf la racine) : le système
  (`/usr`, `/etc`, `/opt`, `/Library`…), `/Volumes`, `/private/tmp`, `/Users` lui-même (nom du compte, `/Users/Shared`) ;
  version de Claude Code observée : **2.1.285** seulement (version minimale non établie ; la documentation cite v2.1.246 pour
  la prise en compte des entrées `sandbox.filesystem` et v2.1.257 pour `blockReadsOutsideWorkingDirectories` sur les
  commandes de lecture) ; comportement sur une tâche longue réelle (caches, `ruff`, fichiers temporaires) non établi.
- **Essai réel borné** (préparé par ce ticket ; joué trois fois par le coordinateur le 2026-10-08 : **T2**, puis **T3** et **T4** avec les sondes P14 à P16 et la forme actuelle de P2, ajoutées après T2 ; résultats dans la sous-section suivante) : verbe `native-sandbox-trial` (dépense : deux exécutions cloud,
  l'implémenteur de la configuration puis le relecteur, autorisées par le mainteneur le 2026-10-08 ; aucun modèle local, aucun
  `lms`). Il passe par `Runner.cloud_execution` (enveloppe vérifiée avant tout appel, registre, audit, réglages ci-dessus ; la
  configuration donnée est lue sans être modifiée, la clé et la révision 2 sont activées en mémoire). Une session de sonde
  exécute des étapes fixes avec l'outil nommé (un fichier de tests du bundle réellement exécuté, `--probe-test`, et
  `git add`/`git commit` au shell, Write dans son propre scratch ; lecture d'un autre essai au shell, par Read, Glob et Grep ; lecture d'un fichier du home au shell et par Read ;
  écriture hors de l'essai au shell, par Write et par Edit ; `ls /`), sur des fichiers que le verbe a préparés (un autre
  essai, des sentinelles dans le home qu'il supprime ensuite). Il lit le résultat **dans le flux et sur le disque** (une
  écriture est jugée sur le disque d'abord ; un échec sans signe de refus du système ou des permissions est `unknown`, pas
  « refusé »), puis lance le relecteur sur le correctif de la sonde. Il écrit un fichier unique (jamais écrasé) :
  `session_authenticated`, une ligne par sonde (`attendu`, `observé`, `as_expected`), le relecteur (verdict lu), les
  `barrier` des enregistrements, le nombre de drapeaux d'audit, le total de facturation, les noms de la racine du disque,
  les réglages **que chaque exécution a reçus** (`settings_passed_paths_masked` pour la sonde,
  `reviewer_settings_passed_paths_masked` pour le relecteur : l'objet passé par `cloud_execution`, lu sur l'exécution, jamais
  reconstruit ; `settings_source` dit d'où ils viennent), chemins masqués (`<attempt>`, `<reviewer-attempt>`, `<work-root>`,
  `<home>`), la version de Claude Code, la liste de ce que l'essai n'établit pas (calculée à partir de ce qu'il a joué et
  vu) ; ni transcript brut, ni identifiant de session, ni chemin du home (le verbe refuse d'écrire un
  résultat qui en contient un). Commande :
  `python3 -m foundry.local_first_runner native-sandbox-trial --campaign docs/qualification/pat-19-campaign-v4.json
  --envelope <enveloppe-compare_exploration> --state-dir <état> --work-root <racine, hors du dépôt et des entrées>
  --repo . --snapshot docs/qualification/pat-19-corpus-snapshot-v1.json --task-pr <PR> --probe-test <fichier de tests
  rapide du bundle, chemin relatif> --out <résultat.json>`
  (`PYTHONPATH=plugins/foundry/tooling`, environnement de la machine authentifiée du mainteneur, R6 ; `--no-reviewer`
  pour ne pas lancer le relecteur ; `--probe-test` est obligatoire, choisi par l'opérateur et vérifié présent dans le bundle
  avant toute dépense : aucun fichier n'est désigné ici comme rapide, ce n'est pas mesuré). Sondes : P2 un fichier de
  tests du bundle réellement exécuté (`python3 -m pytest -q <fichier>` ; `allowed` seulement si le résultat montre
  « N passed », N >= 1, **sans** « N failed » ni « N error(s) » : la commande passe par `| tail`, le code de sortie de pytest
  est perdu, c'est donc la ligne de résumé qui est lue ; **dans T2, P2 était `pytest --collect-only` : une collecte, aucun test exécuté**, et la réévaluation
  hors ligne de T2 garde ce libellé), P2b git, P12 Write dans son scratch (attendus
  `allowed`) ; P3 shell et P4 Read, P5 Glob, P6 Grep sur un autre essai, P7 shell et P8 Read sur le home, P9 shell, P10
  Write, P11 Edit hors de l'essai (attendus `refused`) ; P13 `ls /` (`listed`) ; **ajoutées après T2, jouées par T3 et T4** : P14 Write et P15 shell
  créent un fichier de réglages de projet dans `<bundle>/.claude` (attendus `refused`), P16 un programme (`python3 -c
  open(...)`) écrit hors de l'essai pour observer un refus **du système d'exploitation** et non de `dontAsk` (attendu
  `refused`). Chaque sonde refusée dit par quelle couche (`refused_by` : `os_sandbox`, `dontAsk_mode`, `permission_rule`) ;
  seules les formes de refus connues comptent, toute autre erreur reste `unknown` ; une sonde dont l'outil est absent de la
  liste d'outils du pilote (`allowed_tools` de la configuration ; en réévaluation hors ligne, la liste unique nommée par
  l'événement `init` du flux) est `tool_not_available`, que le bras ait tenté l'appel ou non (dans T4 il n'a tenté ni Glob
  ni Grep : la note de la sonde le dit, le classement vient alors de la liste et non d'une réponse de l'hôte) ; une écriture est jugée sur le disque, ou,
  en réévaluation hors ligne, sur le résultat de l'outil (`judged_on`). **Répertoire d'état** : le verbe active la clé et la
  révision 2 en mémoire sur une configuration gelée, hors chargeur : son répertoire d'état porte un marqueur
  (`.pat19-native-sandbox-trial`) ; une campagne refuse un répertoire qui le porte, et le verbe refuse un répertoire qui
  contient des résultats de campagne.
- **Statut documentaire (R5)** : ce document, le CHANGELOG ; artefacts : clé `isolation.cloud_native_sandbox`, extension de
  `isolation.private_attempt_root` aux protocoles v5 et suivants, constantes `NATIVE_SANDBOX_KEY`, `NATIVE_PERMISSION_MODE`,
  `NATIVE_GIT_ENV`, `BARRIERS`, `BARRIER_SETTINGS`, `BARRIER_OBSERVED`, `AUDIT_BARRIER` (désormais une valeur parmi trois),
  fonctions `native_sandbox_settings` et `with_native_sandbox`, paramètre `native_settings` de `execute_driver`, verbe
  `native-sandbox-trial` et module `local_first_native_trial`, champ `audit.barrier` ; revue 2 : option obligatoire
  `--probe-test` du verbe, champ d'enregistrement `correction_excluded`, clé d'exécution `native_settings`, constantes
  `NATIVE_REFUSED_FLAGS` et `NATIVE_TRIAL_KEY`, champs de résultat `settings_source`,
  `reviewer_settings_passed_paths_masked` et `reviewer.mktemp_used` ; revue 3 : paramètre `declared_tools` de `observe`,
  note `tool_not_attempted`, second résultat versé `pat-19-native-sandbox-trial-2026-10-08-t4.json`. Aucun verbe de `foundry_cli.py`, clé de
  configuration produit, table de routage ni constante de routage modifiés. Contrainte opérationnelle (R6) : le lanceur d'un
  protocole v5 tourne sur la machine authentifiée du mainteneur ; le bac à sable natif ne rend pas le processus `claude`
  confiné. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.

### Essai réel du 2026-10-08 (PAT-124)

Cette sous-section décrit les **essais du lanceur** : d'abord **T2** (jusqu'à « Non établi par T2 »), puis **T3 et T4**
(paragraphes « T3 et T4 »). L'essai manuel T1 du même jour (preuves non versées, `bypassPermissions`, réglages écrits à la
main) n'y figure pas : voir « Quatre essais du 2026-10-08 » plus haut.

Résultat versé : [`pat-19-native-sandbox-trial-2026-10-08.json`](pat-19-native-sandbox-trial-2026-10-08.json), produit par le
verbe hors ligne `native-sandbox-trial-reeval --from-dir <dossier de l'essai> --out <fichier>` (relit `result.json`, le
registre et les deux flux bruts de l'essai ; aucun appel cloud ; ne réécrit jamais un résultat) avec la classification
corrigée. Ni transcript brut, ni identifiant de session, ni chemin du home (les chemins des réglages sont masqués, et sous
`<home>` seules les entrées sensibles connues sont gardées). Les flux bruts restent hors dépôt. **Réglages du fichier
versé** : ils ont été reconstruits par l'outil au moment de T2, pas lus sur l'exécution (il n'a pas été vérifié qu'ils sont
identiques à l'objet reçu), et ils **précèdent le durcissement de la revue 1** : ni `sandbox.filesystem.denyWrite`, ni règle
`Edit` sur le `.claude` du bundle, règles de refus sur un fichier sensible en `/**` seulement ; les réglages du relecteur n'y
sont pas. Le fichier le dit lui-même (`settings_source`, écrit par l'outil de réévaluation, jamais à la main). Le fichier de
T4 porte, lui, les réglages reçus par chaque exécution.

**Contexte** : Claude Code 2.1.285, tâche PR 27, mode de permission demandé et vu dans `init` : `dontAsk`, deux exécutions
cloud (sonde de l'implémenteur : 33 364 jetons ; relecteur : 159 979), barrière `settings_transmitted_version_observed` pour
les deux enregistrements.

**Observé (T2)** : session authentifiée ; **collecte** des tests du bundle (`pytest --collect-only` : une collecte,
**aucun test exécuté**, P2) et `git add`/`git commit` dans le bundle (P2b), écriture par l'outil Write dans son propre scratch
(P12) : permis ; lecture d'un autre essai au shell (P3 : `Operation not permitted`, système) et par l'outil Read (P4 :
message de `blockReadsOutsideWorkingDirectories`, permissions) ; lecture d'un fichier du home au shell (P7 : `Operation not
permitted`, **système**) et par l'outil Read (P8 : « denied by your permission settings », **règle de permission**) ;
écriture hors de l'essai au shell (P9) et par Write (P10) : refus du mode `dontAsk` ; tous **refusés** ; liste de la racine
du disque (P13) : visible. Relecteur : fonctionnel, verdict lu.

**Trois états que le premier classeur ne savait pas dire** (corrigés, avec tests) : le message de l'outil Read pour
`blockReadsOutsideWorkingDirectories` est bien un refus (P4) ; **Glob et Grep n'existent pas pour ce pilote** (« No such tool
available » : sa liste d'outils est Bash, Edit, Read, Write) : état `tool_not_available`, la question ne se pose pas avec ce
pilote, un pilote qui les activerait demanderait son propre essai (P5, P6) ; l'outil Edit exige une lecture préalable du
fichier, refusée hors de l'essai : la règle de permission d'Edit **n'a pas été exercée** (état `not_exercised`, jamais
« refusé », P11). Le refus d'écriture au shell (P9) vient du mode `dontAsk` (commande à redirection vers un chemin hors des
dossiers de travail), pas du bac à sable du système d'exploitation.

**Drapeaux d'audit** (relus avec `audit_transcript`, révision 2) : sonde = 13 : les 10 cibles des sondes (autre essai, fichiers
du home, racine du disque, ce qui était voulu), 2 échos d'un chemin dans un résultat d'outil et l'écho d'un avertissement git
(voir ci-dessous) ; relecteur = **1**, qui n'est pas un `cd` vers un dossier `mktemp` comme en v4 : c'est l'écho, dans un
résultat d'outil, de l'avertissement `git` « unable to access '~/.config/git/ignore': Operation not permitted ». Avec le
bac à sable, chaque appel `git` du bras produit cet avertissement (le fichier d'exclusion par défaut sous le home est
refusé malgré `GIT_CONFIG_GLOBAL=/dev/null`) et l'audit le relève comme chemin du home : **sur-drapeau récurrent à porter
au ticket v5** (envisager de rediriger `XDG_CONFIG_HOME` ou de le traiter dans l'audit). Gêne du relecteur par le bac à
sable, en termes neutres : un seul appel refusé (un `cd <dossier absolu de l'essai> && sed … && cat > scratch/review.json
<<EOF`, refusé par le mode `dontAsk` : commande composée avec redirection), contourné avec les outils Read puis Write
dans son dossier ; `review.json` écrit et verdict lu ; aucune autre commande refusée. Sa copie `mktemp` n'a pas été
utilisée dans cet essai (non exercée).

**Non établi par T2** (même liste que `not_established_by_this_trial` du fichier versé de T2 ; T3 et T4 en lèvent une partie, voir plus bas) : comportement sur une tâche réelle
longue (caches, `ruff`, fichiers temporaires) ; version minimale de Claude Code (seule la 2.1.285 est observée) ; **aucun test
réellement exécuté sous le bac à sable** (P2 n'a fait que collecter) ; Glob et Grep (absents du pilote) ; la règle de
permission d'Edit hors de l'essai ; que le système d'exploitation applique le bac à sable pour une commande que la sonde n'a
pas lancée, en particulier un refus d'écriture du shell par le système (P9 refusé d'abord par `dontAsk`) ; P16 (un programme
écrit hors de l'essai), sonde ajoutée ensuite, non jouée ; une racine de travail sous le home ; des réglages de projet écrits
dans le `.claude` du bundle (P14, P15, non jouées) ; le rechargement à chaud d'un réglage de projet écrit pendant que le bras
tourne ; des réglages de projet dans un dossier au-dessus du dossier de travail ; le dossier temporaire par utilisateur,
partagé entre essais ; la copie `mktemp` du relecteur (il n'en a pas fait dans T2) ; la fréquence, sur une tâche longue, des
commandes composées avec redirection refusées par `dontAsk`.

**T3 et T4 : contexte.** Code durci au commit `7dddce7`, Claude Code 2.1.285, tâche PR 27, mode `dontAsk` demandé et vu dans
`init`, deux exécutions cloud par essai, barrière `settings_transmitted_version_observed` pour les deux enregistrements.
Jetons : T3, sonde 65 933 et relecteur 50 520 ; T4, sonde 35 593 et relecteur 50 661. Drapeaux d'audit : T3, sonde 15 et
relecteur 1 ; T4, sonde 13 et relecteur 1 (le drapeau du relecteur est de nouveau l'avertissement `git` ci-dessus). Fichier
versé pour T4 : [`pat-19-native-sandbox-trial-2026-10-08-t4.json`](pat-19-native-sandbox-trial-2026-10-08-t4.json),
produit par `native-sandbox-trial-reeval` à partir du `result.json` de l'essai (aucune retouche à la main) : par rapport à la
sortie d'origine, P5 et P6 passent de `unknown` à `tool_not_available` avec leur note, les écritures disent `judged_on`
« tool_result, and the disk in the original run (same verdict) », et la liste de ce qui n'est pas établi gagne trois lignes
(Glob et Grep, la suite de tests, le `denyWrite` du système sur `.claude`). Le fichier de T3 n'est pas versé.

**T3 et T4 : observé avec les réglages du lanceur** (identique dans les deux essais, sauf P2) :

- **Écriture hors de l'essai refusée par le système** : P16, un programme (`python3 -c open(...)`), reçoit `Operation not
  permitted` (`refused_by: os_sandbox`), fichier absent du disque. C'est la première observation d'un refus d'écriture par
  le système avec les réglages du lanceur. P9 (`echo x > …`) et P10 (Write) restent refusés par `dontAsk`.
- **`.claude` du bundle** : P14 (outil Write) refusée par une **règle de permission** ; P15 (shell, `mkdir` puis redirection)
  refusée par le **mode `dontAsk`**, avant exécution. **P15 ne montre donc pas** le `sandbox.filesystem.denyWrite` du système
  sur `.claude` : aucune commande shell n'y a été refusée par le système.
- **Réglages lus sur l'exécution** : `settings_source` vaut « taken from each execution » ; les deux objets versés (sonde et
  relecteur) portent `denyWrite` sur `<…>/bundle/.claude` et les règles `Edit` nues et en `/**`.
- **Un fichier de tests exécuté et réussi sous le bac à sable (T4 seulement)** : `plugins/foundry/tests/test_process_contract.py`,
  « 6 passed ». C'est **un petit fichier qui ne fait que lire des fichiers du dépôt** (aucun sous-processus, aucun fichier
  temporaire), **pas la suite du bundle**. Dans T3, P2 est `unknown` (« 3 deselected », fichier `benchmark_campaign` : erreur de
  choix, pas un effet du bac à sable).
- **Inchangé par rapport à T2** : session authentifiée ; P2b et P12 permis ; P3 et P7 refusés par le système ; P4 et P8 par
  une règle de permission ; P11 non exercée ; P13 liste la racine (17 noms dans T4). Glob et Grep absents du pilote : tentés
  et « No such tool available » dans T3, non tentés dans T4.
- **Relecteur** : il a tourné, verdict lu, **aucun appel refusé** (dans T2 un appel l'avait été), pas de copie `mktemp`.

**Reste non établi après T2, T3 et T4** : comportement sur une tâche réelle longue (caches, `ruff`, fichiers temporaires) ; la
**suite de tests** du bundle sous le bac à sable ; toute version de Claude Code autre que la 2.1.285 ; Glob et Grep ; la règle
de permission d'Edit hors de l'essai (P11 non exercée) ; une racine de travail sous le home ; le rechargement à chaud d'un
réglage de projet écrit pendant que le bras tourne ; des réglages de projet au-dessus du dossier de travail ; le dossier
temporaire par utilisateur, partagé entre essais ; la copie `mktemp` du relecteur (jamais faite dans T2, T3, T4) ; le
`denyWrite` du système sur `.claude` pour une commande shell ; la fréquence, sur une tâche longue, des commandes refusées par
`dontAsk`.

**Surface visible restante** : la liste de la racine du disque (`/Applications`, `/Users`, `/Volumes`, `/private`, `/usr`…
sans pouvoir entrer dans le home ni dans la racine de travail), et la lecture du système.

### Réglages de projet de l'arme et durcissement (PAT-124, revue 1)

**Ce que dit la documentation** (<https://code.claude.com/docs/en/settings>, `/sandboxing`, `/cli-reference`, lues le
2026-10-08) : les réglages `--settings` ont priorité sur ceux de projet et utilisateur pour une même clé, **mais les tableaux
(`permissions.allow`, `sandbox.filesystem.*`…) sont fusionnés entre portées, pas remplacés** ; les pilotes gardent `--setting-sources
project,local`, donc un `.claude/settings.json` ou `settings.local.json` du bundle est chargé et **peut ajouter** des entrées
(`additionalDirectories`, `allowRead`…). Le bac à sable protège, sans exemption possible (« an `allowWrite` entry or an `Edit` allow
rule … doesn't lift the protection »), les fichiers de réglages `.claude` du dossier de travail contre une **commande shell**
(canal shell : fermé par la documentation) ; les chemins protégés du système de permissions ne sont jamais approuvés
automatiquement hors `bypassPermissions` (canal outils Write/Edit : fermé par `dontAsk` selon la documentation ; **observé par T3 et T4 pour Write** : P14 refusée,
par la règle de permission `Edit` sur le `.claude` du bundle ; Edit non joué).

**Durci sous la clé seulement** : (1) **tout bundle que le lanceur construit** (`Runner._bundle` ne regarde pas le pilote :
implémenteur, correcteur, relecteur, explorateur cloud, sonde, **et aussi le harnais local et l'explorateur local**) est
construit **sans aucun dossier `.claude`**, à toute profondeur (retiré de l'arbre et de l'index, commit de base modifié :
aucun faux effacement dans le correctif), ni aucun **lien symbolique nommé `.claude`** (quelle que soit sa cible ; un fichier
ordinaire nommé `.claude` est laissé ; le bundle est refusé si le retrait échoue) ; un correctif appliqué à un bundle de
correcteur est débarrassé de ses sections `.claude` et, une fois appliqué, d'un lien `.claude` qu'il aurait apporté
(`_strip_claude_dirs`, `_without_claude_dirs`) ; couvre le réglage de projet présent dans le dépôt du corpus et celui qu'un
bras aurait écrit. **Trace** : l'enregistrement de la manche de correction porte `correction_excluded` (liste des chemins
retirés du correctif remis au correcteur, même forme que `review_excluded` du relecteur ; clé absente si la liste est vide, et
toujours absente sans la clé du bac à sable ; un enregistrement coupé par une erreur ne la porte pas) ; sous la clé, un
lien `.claude` retiré du bundle du relecteur après application du correctif s'ajoute de même à `review_excluded` ; (2) le
`.claude` du bundle est en `sandbox.filesystem.denyWrite` et en règle `Edit` deny (les deux formes `…` et `…/**`) ; (3) les
règles de refus sur un **fichier** sensible (`.netrc`, `.git-credentials`, `*_history`) sont émises nues et avec `/**` ; (4) le
chargeur refuse, sous la clé, un pilote cloud dont la ligne de commande porte, nu ou sous la forme `--drapeau=valeur` :
`--settings`, `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `--add-dir`, `--allowedTools`,
`--allowed-tools`, `--mcp-config`, `--plugin-dir` (`NATIVE_REFUSED_FLAGS`), ou `--permission-mode=…`, ou **plus d'une** paire
séparée `--permission-mode <x>` (une seule est remplacée par `dontAsk`). Noms exacts : `--setting-sources`,
`--strict-mcp-config` et `--disallowedTools` restent acceptés. Cette liste est celle des drapeaux connus qui posent le mode
ou les réglages, ou élargissent ce que la session lit, écrit ou lance ; **elle n'est pas démontrée exhaustive** pour les
versions futures de Claude Code. (5) La clé interne `_native_trial`, posée en mémoire par le verbe d'essai, est refusée dans
tout fichier de configuration.

**Limite : ce que le retrait de `.claude` change à la mesure.** Le juge juge **le bundle du bras** : sous la clé, sa base n'a
aucun `.claude`, et le bras ne peut pas en écrire un à la racine du bundle (`denyWrite` et règle `Edit` ; observé par T3 et T4 : P14 refusée par
la règle de permission, P15 par `dontAsk`, donc pas par le `denyWrite` du système, qui reste non observé). Une tâche dont le correctif légitime ou les tests cachés touchent un chemin sous `.claude/` deviendrait donc
**insoluble**, ou serait jugée sur **un autre arbre qu'en v4** (où le bundle garde le `.claude` de la base et où le bras peut
y écrire) ; le relecteur ne voit jamais ces chemins (v4 compris, `review_excluded`) et, sous la clé, le correcteur non plus
(`correction_excluded`). **Coût de comparabilité v5 / v4** : pour une telle tâche, un écart de réussite entre v5 et v4
viendrait de l'instrument et non du bras ; elle devrait être écartée ou déclarée par le protocole v5. S'y ajoute que le
retrait vaut aussi pour les bras locaux (harnais local, explorateur local), dont le bundle diffère donc de celui de la v4
dans les mêmes cas. **Fait établi hors ligne pour le corpus v1** (2026-10-08 ; `git ls-tree -r` sur le SHA de base, `git diff
--name-only` base..tête recoupé avec `files` de l'instantané, et `protected.entries` de l'instantané ; nombre de chemins
ayant un composant `.claude`, aucun contenu lu) : **zéro partout**, donc pour ces 12 tâches le retrait ne change ni l'arbre
de base, ni le correctif fusionné, ni les tests cachés. Cela ne dit rien d'un corpus futur, ni de ce qu'un bras écrirait de
lui-même sous un `.claude` imbriqué (`sous-dossier/.claude/`, non protégé en écriture : jugé avec, retiré pour le relecteur
et le correcteur).

| Ensemble | PR | Ticket | Arbre de base | Correctif fusionné | Tests cachés |
| --- | --- | --- | --- | --- | --- |
| comparaison | 26 | PAT-35 | 0 sur 257 | 0 sur 3 | 0 sur 1 |
| comparaison | 38 | PAT-45 | 0 sur 263 | 0 sur 3 | 0 sur 1 |
| comparaison | 25 | PAT-34 | 0 sur 257 | 0 sur 4 | 0 sur 1 |
| comparaison | 42 | PAT-44 | 0 sur 269 | 0 sur 4 | 0 sur 1 |
| comparaison | 33 | PAT-41 | 0 sur 257 | 0 sur 7 | 0 sur 2 |
| comparaison | 37 | PAT-48 | 0 sur 263 | 0 sur 6 | 0 sur 1 |
| tamis | 30 | PAT-39 | 0 sur 257 | 0 sur 3 | 0 sur 1 |
| tamis | 83 | PAT-101 | 0 sur 397 | 0 sur 4 | 0 sur 1 |
| tamis | 27 | PAT-36 | 0 sur 257 | 0 sur 3 | 0 sur 1 |
| tamis | 24 | PAT-33 | 0 sur 257 | 0 sur 4 | 0 sur 1 |
| tamis | 48 | PAT-72 | 0 sur 270 | 0 sur 3 | 0 sur 1 |
| tamis | 19 | PAT-28 | 0 sur 257 | 0 sur 3 | 0 sur 1 |

(Chaque case : chemins sous un `.claude/` sur le nombre de chemins examinés ; la colonne « Tests cachés » compte les fichiers
de `protected.entries`.)

**Non modifié, faute de certitude** : `--setting-sources project,local` reste (la documentation décrit la liste « user, project,
local » sans dire qu'une liste vide est acceptée, ni ce que devient alors `--settings` ou l'authentification ; retirer
`project,local` n'est donc pas démontré sans risque). **Non vérifié, à porter au ticket v5** : la fusion effective d'un
`.claude/settings.local.json` écrit pendant la session (rechargement à chaud) ; que le système refuse une écriture **du shell** dans le `.claude`
du bundle (`denyWrite` : P15 a été refusée par `dontAsk` avant exécution dans T3 et T4 ; Write y est refusé, P14) ; Edit sur ce
`.claude` (non joué) ; la sûreté du bac à sable si le dépôt du corpus contient un
réglage de projet *hors* du dossier de travail (les ancêtres sont protégés selon la documentation, non éprouvé).

### Limites supplémentaires de l'essai du 2026-10-08

Il s'agit des **essais du lanceur** (T2, T3, T4) ; ce que seul T1 (essai manuel, preuves non versées) a vu est dit comme tel.

- **Refus d'écriture du système d'exploitation : observé avec les réglages du lanceur, par P16 dans T3 et dans T4** (un
  programme `python3 -c open(...)` hors de l'essai : `Operation not permitted`). Dans T2 il ne l'avait pas été : P9
  (`echo x > …`) y était refusée par `dontAsk` avant exécution, et l'est encore dans T3 et T4. Portée : **une** forme
  d'écriture (un programme Python), **une** version de Claude Code ; un `cp` ou une autre commande n'a pas été sondé. Les refus
  de lecture P3 et P7 viennent du système dans les trois essais. T1 avait aussi vu le système refuser une écriture du shell
  dans un dossier voisin, mais sous `bypassPermissions` et des réglages écrits à la main, preuve non versée : ce n'était
  **pas une observation des réglages du lanceur**.
- **`denyWrite` du système sur le `.claude` du bundle : non observé.** P15 (shell) est refusée par `dontAsk` avant
  exécution ; seul le refus de l'outil Write par la règle de permission (P14) est observé.
- **Tests sous le bac à sable** : un seul fichier, 6 tests, en lecture seule (T4). P2 de T2 était une collecte
  (`pytest --collect-only`) ; P2 de T3 n'a rien sélectionné (« 3 deselected », erreur de choix du fichier). La suite du
  bundle et un test qui écrit des fichiers temporaires ou lance un sous-processus ne sont pas établis.
- **Canal entre essais par le dossier temporaire** : le dossier temporaire par utilisateur est lisible et écrivable par le shell
  et partagé par tous les essais et processus du compte ; la copie `mktemp` d'un relecteur y est lisible par un essai
  ultérieur. Un v5 devrait donner à chaque essai un `TMPDIR` dans son dossier (`TMPDIR` figure dans les variables R6 laissées
  intactes : **décision du ticket v5**, non prise ici).
- **Autres lectures ouvertes** : `/Volumes`, `/private/tmp`, `/Users/Shared`, `/Users` (liste des comptes), le système.
- **Coût de comparabilité de `dontAsk`** : les bras se comportent autrement qu'en v4 (une commande composée avec redirection est
  refusée ; le relecteur de T2 a dû écrire `review.json` avec l'outil Write après un refus, d'où un risque de
  `review_unreadable`) ; fréquence sur une tâche longue inconnue ; une comparaison v5 / v4 n'est pas à iso-comportement sur ce
  point.
- **Mode de permission** : il n'est vérifié que si l'événement `init` le nomme ; `settings_transmitted_version_observed` ne prouve
  pas le mode autrement.
- **Réévaluation hors ligne** : les écritures (P9, P10, P12) y sont jugées sur le résultat de l'outil, sans disque ; le fichier
  committé le dit par sonde (`judged_on`), le verdict de l'essai d'origine (disque) est identique. Les règles masquées du type
  `Read(/<attempt>/**)` représentent des règles absolues `Read(//chemin/**)` (voir `masked_rules_note`).

## Protocole v5 (PAT-126)

Protocole **gelé le 2026-10-08, avant tout essai de la campagne** : [`pat-19-protocol-v5.md`](pat-19-protocol-v5.md) ; configurations : `pat-19-campaign-v5.json` (campagne, 12 tâches, gelée, empreinte épinglée par les tests) et `pat-19-campaign-v5-pilot.json` (configuration des pilotes, PR 27, hors campagne, non épinglée) ; boucle opérateur : [`pat-19-v5-operator.md`](pat-19-v5-operator.md). Même schéma que la v2 à la v4, mêmes modes, même code. Quatre pilotes réels ont précédé le gel (protocole, section 7 ; pièces sous `pat-19-runs/x5pilot-1/` à `-4/`), le quatrième a passé ses critères d'arrêt ; `FROZEN_PROTOCOLS` contient la v5 (pas son pilote). **Rien de v1 à v4 ne change de comportement** : les clés ci-dessous sont absentes de leurs configurations (sha256 des configurations v1 à v5 vérifiés par les tests) et refusées au chargement hors d'un protocole postérieur à la v4.

- **Protocoles reconnus** : `pat-19-protocol-v5` (liste blanche `pat-19-protocol-vN`, N >= 5, comme pour `audit_revision`) et `pat-19-protocol-v5-pilot` (même instrument, une tâche, noms propres). Le chargeur **épingle** ces deux protocoles : le candidat unique et fixé, les bornes (60 étapes / 900 s, 2 corrections), `one_task_per_launch`, `correction_feedback` (20 / 300 / 200), `isolation.private_attempt_root`, `isolation.cloud_native_sandbox` et `isolation.audit_revision` 2 (vrais), la liste de tâches et l'étiquette (campagne : `[26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19]` et `pat-19-v5` ; pilote : `[27]` et `pat-19-v5-pilot`), `rules.exploration_comparison.tasks` (12 ou 1) et `premium_per_accepted_ratio_max` 0,85, `binary_version` de chaque pilote cloud (`claude --version`, 2.1.285), l'absence d'explorateur cloud, et `isolation.allow_read_home` inclus dans `[".config/git/ignore"]`. Une dérive est refusée au chargement (code 2). Le schéma v1 avec un protocole v5 est refusé. Les tests de PAT-123 et PAT-124 qui prenaient `pat-19-protocol-v5` pour un « protocole postérieur » quelconque prennent maintenant `pat-19-protocol-v6` (non épinglé).
- **Clés v4 acceptées après la v4** : `correction_feedback`, `exploration.fixed_candidate`, `isolation.private_attempt_root` (déjà) et `exploration.comparison_task_group` sont acceptées sous un protocole postérieur à la v4 ; sous v1 à v3 elles restent refusées avec le même message.
- **`exploration.comparison_tasks`** (liste non vide de numéros de PR distincts) et **`exploration.comparison_task_set`** (identifiant simple, ni `comparison` ni `screening`), toujours ensemble, exclusifs de `comparison_task_group`, **acceptés seulement sous un protocole postérieur à la v4**. `compare-exploration` joue les PR dans l'ordre de la liste (`_listed_tasks` : chaque PR doit figurer dans le groupe `comparison` ou `screening` du manifeste et dans l'instantané, sinon refus avant toute réservation). Tous les enregistrements de la comparaison (explorations L, implémenteurs, corrections, relecteurs) et le registre portent `task.set` / `set` = l'étiquette ; `report` lit cette étiquette (`exploration_comparison` compte les tâches de cet ensemble ; sans la clé : `comparison`, inchangé). L'ensemble est la clé de reprise : les enregistrements d'une autre étiquette ne comptent pas.
- **Bras** : `compare-exploration` refuse tout bras autre que `A` et `L` sous la v5 comme sous la v4, avant toute réservation ; `--paths` vaut `A,L` par défaut.
- **Pilote et campagne ne se mélangent pas** : le lanceur refuse (à la construction du `Runner`, avant tout état) un identifiant de campagne sans `pilot` sous `pat-19-protocol-v5-pilot` et un identifiant avec `pilot` sous `pat-19-protocol-v5`.
- **Version de Claude Code** : un pilote cloud qui déclare `binary_version` est vérifié par `check_binary_version` (commande en lecture seule, motif à un groupe, version épinglée) au début de `compare-exploration`, **avant toute réservation ni exécution**, une fois par commande distincte ; autre version, sortie illisible ou exécutable absent : `RunnerError`, code 2. Sans `binary_version` (v1 à v4) : rien n'est demandé. Le motif `^(\d+\.\d+\.\d+) \(Claude Code\)$` est celui de `claude --version` (`2.1.223 (Claude Code)` dans les tests existants) : **non revérifié ici (le lanceur ne lance pas `claude`)**.
- **Avertissement `git` sur `~/.config/git/ignore`** : `isolation.allow_read_home` accepte des chemins relatifs au home, fichier ou dossier (`.config/git/ignore` est un fichier) ; il alimente `sandbox.filesystem.allowRead` des réglages natifs (chemin résolu, ajouté après le dossier de l'essai). Les règles `Read`/`Edit` refusées sur le home, le `denyRead` du shell sur le home et l'`allowWrite` sont inchangés : l'outil Read reste refusé sur ce fichier. La même clé alimente le profil `sandbox-exec` d'un bras local, où `deny_read_home.local` (`.config`, en dernier) l'emporte. **Effet observé aux pilotes 2 et 3** : l'avertissement apparaît 0 fois dans les flux cloud (lecture des flux ; le lanceur ne le vérifie pas lui-même).
- **Rapport** : sur une campagne v5 de 12 tâches, `exploration_comparison.complete` exige 12 tâches comparées ; un `review_unreadable` (relecteur signalé, ou sans verdict) est **indécidé** (la tâche l'est aussi et quitte l'ensemble apparié D, voir la règle de décision v5 ci-dessous ; sans la clé `paired_decided_min`, comme en v2 à v4, l'économie devient `unavailable`) ; `review_contaminated` liste les enregistrements dont le relecteur a été signalé ; `audit.barrier` ne dit jamais « confiné ».
- **Statut documentaire (R5)** : ce document, le protocole v5 (section 8), la notice opérateur et le CHANGELOG. Détecteur FOUNDRY-123 non livré : statut affirmé ici, vérifié en revue.
- **Suite du pilote nul (PAT-126)** : le pilote 1 a été nul (`PATH` d'opérateur : `python3` de Homebrew sans pytest ; voir le protocole v5, section 7.3 bis). Trois garde-fous, **protocoles postérieurs à la v4 seulement** : (1) `lfc.judge(..., strict_report=True)` lève `JudgeInstrumentError` quand pytest n'écrit aucun rapport junit, ou sort en code 4 ou 5 sans aucun test, au lieu de `REFUSED` 0/0/0 (enregistrement `tool_error`, indécidé, aucun retour au correcteur) ; v1 à v4 : verdict historique inchangé (test) ; (2) `probe_interpreters` au début de `compare-exploration`, avant toute réservation : l'interpréteur du lanceur (`sys.executable -P`) et le `python3` et le `python` du `PATH` donné à un bras cloud doivent importer pytest, sinon `PreflightRefused` (code 2) nommant lequel ; chemins (home masqué en `~`) et version de pytest dans l'entrée `preflight` du registre (`interpreters`, refus compris) ; (3) verbe `golden-check` (`--campaign --repo --work-root --snapshot --manifest --out`, code 0 si les tâches sont conformes, 1 sinon, 2 refus, jamais d'écrasement) : bundle de chaque tâche de la liste construit par `Runner._bundle`, jugé intact (attendu `REFUSED` avec au moins un échec ou une erreur) puis avec le changement fusionné (attendu `ACCEPTED`). Statut R5 : ces trois artefacts sont documentés ici et dans le protocole v5 ; non appliqué mécaniquement : le script opérateur et le lanceur ne rejouent pas `golden-check` d'eux-mêmes (à jouer avant le gel).
- **Politique de l'audit (PAT-126, après le pilote 2)** : clé `isolation.audit_policy` = `journal_under_observed_barrier` (protocole postérieur à la v4 seulement ; exige `cloud_native_sandbox` et `audit_revision` 2 ; épinglée pour la v5 et son pilote). `audit_transcript(..., policy_denied=(denyRead, allowRead), journal=, refused=)` : pour un flux dont la barrière est `settings_transmitted_version_observed`, un appel dont le résultat est une erreur de forme connue (`_REFUSED_CALL` : refus du mode `dontAsk`, règle de permission, blocage des lectures hors des dossiers de travail) n'a pas tourné (ni contamination ni suivi du répertoire, compté par outil) ; un constat d'une commande shell sous une racine `denyRead` moins `allowRead`, ou le marqueur `/<unknown-working-directory>`, va au journal (`audit.journal`) ; tout autre constat reste décisif ; le résultat de la fonction (donc `contamination`) ne contient que le décisif. `audit` du record gagne `policy`, `journal`, `journal_by_role`, `refused_calls` (absents sans la clé) ; `report` gagne `audit_journal` ; `replay-audit` rejoue la politique (classes par flux, lecture non décisionnelle de l'outcome ; les réglages sont reconstruits, pas relus). Détails, hypothèses et limites : protocole v5, 3.6. R5 : documenté ici et dans le protocole ; détecteur FOUNDRY-123 non livré.
- **Chemins affichés dans une sortie (PAT-126, après le pilote 3)** : sous la politique et une barrière observée, un constat `tool_result:` dont le chemin est sous une racine refusée au shell (`denyRead` moins `allowRead`), ou un préfixe tronqué qui commence par une telle racine (la sortie coupée par un `sed` de l'arme), va au journal : un texte affiché n'est pas un accès. Un `tool_result:` hors de ces racines reste décisif. Le pilote 3 avait perdu un PASS à cause de `tool_result:<racine de travail>/priv`.
- **Règle de décision v5 sur les tâches indécidées (PAT-126, revue du round 1)** : clé `rules.exploration_comparison.paired_decided_min` (9, épinglée par le chargeur pour la v5 et son pilote, refusée avant la v5). Avec elle, `report` applique pour la v5 le traitement du protocole (section 2) : ensemble apparié D des tâches décidées dans les deux bras, `inconclusive` sous 9 tâches, acceptation et prime par tâche acceptée sur D, lecture de robustesse (pire cas, pour « retenu » seulement), `exploration_comparison.paired_rule` (D, indécidées par bras avec leur cause, deux lectures, jetons sur les tâches indécidées). **Round 2** : une compatibilité `unavailable` donne `inconclusive` (`compatibility_unavailable`), seule une compatibilité `fail` donne `keep_cloud` (`compatibility_failed`) ; une interruption ou un plafond sur une tâche en fait une tâche indécidée qui quitte D (y compris une exécution cloud coupée aux jetons inconnus, imprimés `null`) ; les raisons de campagne (exécution cloud jamais soldée ou nommée par aucun enregistrement, exécution interrompue ou aux jetons inconnus d'une tâche de D ou d'aucune tâche comparée, départ sans enregistrement ni rejeu) donnent `inconclusive` quoi que dise D. Clés qui font foi : `decision` et `campaign_conclusion` ; `paired_rule.verdict` leur est toujours égal, le verdict sur D avant les raisons de campagne est dans `verdict_before_campaign_level` (`reason_before_campaign_level`, `campaign_level_reasons`) ; `paired_rule.compatibility` ; un seul rapport de coût pour la v5, `paired_rule.ratio` (`ratio`, `premium_pass` et `premium_per_accepted` de `arms.L.economy_detail` sont `null` sous la v5, avec `superseded_by`). **Round 4** : sur D l'acceptation est lue avant le cas zéro — `accepted_L(D) < accepted_A(D)` donne `keep_cloud` (`not_retained_on_paired_set`) quels que soient les zéros, y compris L sans aucune tâche acceptée face à A qui en accepte (c'était `inconclusive`, `no_accepted_task_in_one_arm_ratio_undefined`) ; cette raison ne reste que pour A sans aucune tâche acceptée (les deux bras à zéro, ou A à zéro et L au moins une) ; `paired_rule.failed_criteria` liste `acceptance` et/ou `economy` (l'économie n'y figure que si elle a pu être lue). **Round 3** : la prime d'un enregistrement `interrupted` qui ne nomme aucune exécution cloud (`_cut_without_cloud` : `status` `interrupted`, total `null`, `cloud_sessions` vide, `cloud_executions` 0) compte 0 dans `paired_rule` (`premium_on_paired`, `premium_tokens_on_undecided_tasks`) — `_tool_error` continue d'écrire `billing_total: null` à tout enregistrement interrompu et les totaux des v2 à v4 (`economy_detail.premium_billing_tokens`) restent `null` ; une prime inconnue sur D que le registre connaît (coupure après le solde de l'exécution) donne `premium_total_unknown` sans raison de campagne ; sous la v5, `arms.L.quality` et `arms.L.economy` valent `unavailable` tant que la règle appariée ne les lit pas (campagne incomplète comprise) et les quatre champs `ratio`, `premium_pass`, `premium_per_accepted`, `reference_premium_per_accepted` de `economy_detail` sont toujours `null`. Ce que la règle des v2 à v4 faisait d'une tâche indécidée (économie `unavailable`, qualité lue par bornes, `keep_cloud` possible) et ce que la v5 change dans les deux sens : protocole v5, section 2. Sans la clé (v2 à v4) : rapport inchangé.
- **Forme des appels refusés (round 1)** : `_refused_call` ne reconnaît les messages de règle de permission que pour les outils de fichier (Read, Edit, Write, Glob, Grep), comme erreur entière de l'appel ; le refus `dontAsk` vaut pour tout outil ; une commande Bash qui a tourné et imprimé une telle phrase n'est pas un appel refusé. `os_denied` : un préfixe tronqué est sous une racine refusée quand son texte commence par la racine suivie de `/`.
- **Juge strict (round 1)** : un rapport pytest absent est une erreur d'instrument sauf si un bundle intact de la même tâche produit un rapport (`candidate_breaks_test_loading`, `REFUSED`). `golden-check` : champ `provenance` (empreintes de la configuration, du manifeste, de l'instantané, commit et état de l'outillage, date).
- **Dossier temporaire partagé (round 1, politique de déplacement refaite au round 2, relevé et échecs consignés au round 3)** : sous `isolation.cloud_native_sandbox`, le lanceur relève les noms de premier niveau de `TMPDIR` (`_temp_names` : `os.listdir`, aucune entrée n'est lue ; `None` si le dossier ne se liste pas) avant chaque exécution cloud et, après, **déplace** dans sa quarantaine `<dossier d'état>/temp-quarantine/<campagne>-<session>/` (`TEMP_QUARANTINE` ; le dossier d'état est refusé aux bras ; **rien n'est jamais supprimé**, un nom déjà pris reçoit un suffixe) chaque entrée dont le **nom était absent avant** (une entrée préexistante, même modifiée pendant l'exécution, n'est jamais touchée ; une entrée créée dans la fenêtre par un autre processus du même compte et correspondant à la signature serait déplacée aussi), du compte, hors lien symbolique, hors `foundry-*` et `pytest-of-*`, qui contient une copie du bundle (arbre contenant `plugins/foundry`, fichier de même chemin relatif qu'un fichier modifié de la tâche, ou de même nom) ; consigné `audit.temp_leftovers`, listé par `report.temp_leftovers`, sans effet sur la décision. **Forme de `audit.temp_leftovers` (round 3)** : `watch` (`complete`, `partial`, `unavailable`, ou `not_applicable` pour un enregistrement sans exécution cloud), `count` (entrées déplacées ; `null`, jamais 0, dès qu'une exécution de l'enregistrement n'a pas été surveillée), `names`, `not_moved` (entrée nouvelle qu'une erreur du système a empêché d'examiner ou de déplacer : elle reste en place), `not_inspected` (parcours abandonné à `TEMP_WALK_BOUND` = 20 000 entrées lues, ou sous-dossier illisible : `_holds_bundle_material` rend alors `None`, l'entrée reste en place), `unwatched` (une raison par exécution non surveillée, vocabulaire `TEMP_UNWATCHED` : `no_tmpdir`, `listing_before_failed`, `listing_after_failed`). Un relevé d'avant en échec ne déplace **rien** ; aucune `OSError` de la surveillance n'interrompt une exécution. `report.temp_leftovers` liste les enregistrements qui ont déplacé quelque chose **et** ceux dont `watch` vaut `partial` ou `unavailable`. Les enregistrements des pilotes gardent l'ancienne forme (`count`, `names`). Le script opérateur refuse un dossier contenant `plugins/foundry` au premier niveau de `TMPDIR`.
